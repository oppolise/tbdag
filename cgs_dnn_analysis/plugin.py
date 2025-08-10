# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# --------------------------------------------------------------------------
import sys
import copy
# pyre-unsafe
import atexit
import json
import os
import shutil
import tempfile
import threading
import time
import re
from collections import OrderedDict
from queue import Queue

import werkzeug
# pyre-fixme[21]: Could not find module `tensorboard.plugins`.
from tensorboard.plugins import base_plugin
from werkzeug import exceptions, wrappers

from . import consts, io, utils
from .profiler import RunLoader
from .run import Run

logger = utils.get_logger()


def decorate_headers(func):
    def wrapper(*args, **kwargs):
        headers = func(*args, **kwargs)
        headers.extend(CGSDNNAnalysisPlugin.headers)
        return headers
    return wrapper

exceptions.HTTPException.get_headers = decorate_headers(exceptions.HTTPException.get_headers)


class CGSDNNAnalysisPlugin(base_plugin.TBPlugin):
    """A simplified profiler plugin to display op tree. (CGS-DNN Analysis: Computational Graph System for Deep Neural Network Analysis)"""

    plugin_name = 'cgs-dnn-analysis'
    headers = [('X-Content-Type-Options', 'nosniff')]
    CONTENT_TYPE = 'application/json'

    def __init__(self, context: base_plugin.TBContext):
        """Instantiates CGSDNNAnalysisPlugin."""
        super(CGSDNNAnalysisPlugin, self).__init__(context)
        if not context.logdir and context.flags.logdir_spec:
            dirs = context.flags.logdir_spec.split(',')
            if len(dirs) > 1:
                logger.warning(f"Multiple directories are specified by --logdir_spec flag. CGSDNNAnalysisPlugin will load the first one: \n {dirs[0]}")
            self.logdir = io.abspath(dirs[0].rstrip('/'))
        else:
            self.logdir = io.abspath(context.logdir.rstrip('/'))

        self._load_lock = threading.Lock()
        self._load_threads = []

        self._runs = OrderedDict()
        self._runs_lock = threading.Lock()
        
        # เพิ่ม cache สำหรับเก็บ operator trees
        self._operator_trees = {}
        self._operator_trees_lock = threading.Lock()

        self._temp_dir = tempfile.mkdtemp()
        self._cache = io.Cache(self._temp_dir)
        self._queue = Queue()

        monitor_runs = threading.Thread(target=self._monitor_runs, name='monitor_runs', daemon=True)
        monitor_runs.start()

        receive_runs = threading.Thread(target=self._receive_runs, name='receive_runs', daemon=True)
        receive_runs.start()

        def clean():
            logger.debug('starting cleanup...')
            self._cache.__exit__(*sys.exc_info())
            logger.debug('remove temporary cache directory %s' % self._temp_dir)
            shutil.rmtree(self._temp_dir)

        atexit.register(clean)

    def is_active(self):
        if self.is_loading:
            return True
        else:
            with self._runs_lock:
                return bool(self._runs)

    def get_plugin_apps(self):
        return {
            '/index.js': self.static_file_route,
            '/index.html': self.static_file_route,
            '/runs': self.runs_route,
            '/workers': self.workers_route,
            '/runtime': self.runtime_route,
            '/dag': self.dag_route,
            '/all_operator_trees': self.all_operator_trees_route,
            '/communication_timing': self.communication_timing_route,
        }

    def frontend_metadata(self):
        return base_plugin.FrontendMetadata(es_module_path='/index.js', disable_reload=True)

    @wrappers.Request.application
    def runs_route(self, request: werkzeug.Request):
        with self._runs_lock:
            names = list(self._runs.keys())
        data = {
            'runs': names,
            'loading': self.is_loading
        }
        return self.respond_as_json(data)

    @wrappers.Request.application
    def workers_route(self, request: werkzeug.Request):
        name = request.args.get('run')
        self._validate(run=name)
        run = self._get_run(name)
        return self.respond_as_json(run.workers)

    @wrappers.Request.application
    def runtime_route(self, request: werkzeug.Request):
        run_name = request.args.get('run')
        worker_name = request.args.get('worker')
        self._validate(run=run_name, worker=worker_name)
        
        # ใช้ข้อมูลจาก cache แทนการเรียก get_operator_tree ใหม่
        with self._operator_trees_lock:
            if run_name not in self._operator_trees:
                raise exceptions.NotFound(f"Run '{run_name}' not found in operator trees cache")
            if worker_name not in self._operator_trees[run_name]:
                raise exceptions.NotFound(f"Worker '{worker_name}' not found in operator trees cache for run '{run_name}'")
            
            # ทำงานบนสำเนาเพื่อไม่แก้ไข cache ต้นฉบับ
            content = copy.deepcopy(self._operator_trees[run_name][worker_name])

        # ปรับแต่งชื่อให้แสดงผลอ่านง่ายเฉพาะตอนส่งให้ frontend
        def prettify_name(name: str) -> str:
            if not isinstance(name, str):
                return name
            s = name
            # ลบ "nn.Module:" แบบตรงไปตรงมา (ทั้งมี/ไม่มีช่องว่าง)
            if 'nn.Module:' in s:
                s = s.replace('nn.Module: ', '').replace('nn.Module:', '')
            # เผื่อมีรูปแบบอื่นตกค้าง ให้ regex เก็บตกอีกชั้น
            s = re.sub(r'\bnn\.Module\s*:\s*', '', s)
            # ลบ namespace บางตัวที่ยาว (aten::, autograd::, torch::)
            s = re.sub(r'^(?:aten|autograd|torch)::', '', s)
            # ย่อ Sequential_# -> Seq#
            s = re.sub(r'^Sequential_(\\d+)', r'Seq\\1', s)
            # ตั้งชื่อ Optimizer ให้สั้นลงเป็นแค่ "Optimizer"
            s = re.sub(r'^Optimizer(?:[.#].*)?$', 'Optimizer', s)
            return s

        def prettify_names_inplace(obj):
            if isinstance(obj, dict):
                name_value = obj.get('name')
                if isinstance(name_value, str):
                    obj['name'] = prettify_name(name_value)
                for v in obj.values():
                    if isinstance(v, (dict, list)):
                        prettify_names_inplace(v)
            elif isinstance(obj, list):
                for item in obj:
                    prettify_names_inplace(item)

        prettify_names_inplace(content)

        # ทำ normalization แยกตามแต่ละ step ให้สเกลอยู่ในช่วง 0..10
        def collect_times(obj, starts, ends):
            if isinstance(obj, dict):
                st = obj.get('start_time')
                et = obj.get('end_time')
                if isinstance(st, (int, float)):
                    starts.append(st)
                if isinstance(et, (int, float)):
                    ends.append(et)
                for v in obj.values():
                    if isinstance(v, (dict, list)):
                        collect_times(v, starts, ends)
            elif isinstance(obj, list):
                for it in obj:
                    collect_times(it, starts, ends)

        def normalize_within_step(step_obj):
            step_starts: list = []
            step_ends: list = []
            collect_times(step_obj, step_starts, step_ends)
            if not step_starts or not step_ends:
                return
            min_start = min(step_starts)
            max_end = max(step_ends)
            span = max_end - min_start
            if span <= 0:
                return
            scale = 10.0 / span

            def apply_norm(obj):
                if isinstance(obj, dict):
                    st = obj.get('start_time')
                    et = obj.get('end_time')
                    if isinstance(st, (int, float)):
                        obj['start_time'] = (st - min_start) * scale
                    if isinstance(et, (int, float)):
                        obj['end_time'] = (et - min_start) * scale
                    if 'start_time' in obj and 'end_time' in obj:
                        obj['dur'] = obj['end_time'] - obj['start_time']
                    for v in obj.values():
                        if isinstance(v, (dict, list)):
                            apply_norm(v)
                elif isinstance(obj, list):
                    for it in obj:
                        apply_norm(it)

            apply_norm(step_obj)

        for step_key, step_content in content.items():
            normalize_within_step(step_content)

        return self.respond_as_json(content)

    @wrappers.Request.application
    def dag_route(self, request: werkzeug.Request):
        """
        สร้างข้อมูล DAG จาก operator trees ใน cache ตามกฎที่ผู้ใช้ระบุ
        - โหนดมี 2 ประเภท: computation (บน) และ communication (ล่าง)
        - ใช้ dur เป็นตัวกำหนดสเกลขนาดเมื่อไปวาดด้านหน้า
        - รวม broadcast ทั้ง step เป็นก้อนเดียว
        - รวม children การสื่อสารของแต่ละ backward (เช่น nccl:all_reduce) เป็นก้อนเดียวต่อ backward
        - สร้างเส้นเชื่อม:
          * โหนด computation → โหนด computation ถัดไป (start >= end ที่ใกล้ที่สุด)
          * โหนด backward → โหนด all_reduce ที่ถูกรวมของมัน (ถ้ามี)
          * ทุกโหนด communication → โหนด computation ถัดไป
        ผลลัพธ์ต่อ 1 step:
        {
          "<step>": { nodes: [ {id,label,category,lane,dur,start_time,end_time} ], edges: [ {source,target,kind} ] }
        }
        """
        run_name = request.args.get('run')
        worker_name = request.args.get('worker')
        self._validate(run=run_name, worker=worker_name)

        # ดึงข้อมูลดิบจาก cache
        with self._operator_trees_lock:
            if run_name not in self._operator_trees:
                raise exceptions.NotFound(f"Run '{run_name}' not found in operator trees cache")
            if worker_name not in self._operator_trees[run_name]:
                raise exceptions.NotFound(
                    f"Worker '{worker_name}' not found in operator trees cache for run '{run_name}'"
                )
            tree = copy.deepcopy(self._operator_trees[run_name][worker_name])

        # ฟังก์ชันช่วยทำชื่อให้อ่านง่ายเทียบกับ runtime_route
        def prettify_name(name: str) -> str:
            if not isinstance(name, str):
                return name
            s = name
            if 'nn.Module:' in s:
                s = s.replace('nn.Module: ', '').replace('nn.Module:', '')
            s = re.sub(r'\bnn\.Module\s*:\s*', '', s)
            s = re.sub(r'^(?:aten|autograd|torch)::', '', s)
            s = re.sub(r'^Sequential_(\d+)', r'Seq\1', s)
            s = re.sub(r'^Optimizer(?:[.#].*)?$', 'Optimizer', s)
            return s

        def prettify_names_inplace(obj):
            if isinstance(obj, dict):
                if isinstance(obj.get('name'), str):
                    obj['name'] = prettify_name(obj['name'])
                for v in obj.values():
                    if isinstance(v, (dict, list)):
                        prettify_names_inplace(v)
            elif isinstance(obj, list):
                for it in obj:
                    prettify_names_inplace(it)

        prettify_names_inplace(tree)

        def duration_of(ev: dict) -> float:
            dur = ev.get('dur')
            if isinstance(dur, (int, float)):
                return float(dur)
            st = ev.get('start_time', 0)
            et = ev.get('end_time', 0)
            if isinstance(st, (int, float)) and isinstance(et, (int, float)):
                return float(et) - float(st)
            return 0.0

        def group_comm_interval(events: list):
            # รวมช่วงเวลาเป็นก้อนเดียวจากกลุ่ม communication ที่ส่งมา
            if not events:
                return None
            starts, ends = [], []
            for ev in events:
                st = ev.get('start_time')
                et = ev.get('end_time')
                if isinstance(st, (int, float)) and isinstance(et, (int, float)):
                    starts.append(float(st))
                    ends.append(float(et))
            if not starts or not ends:
                return None
            st = min(starts)
            et = max(ends)
            return {
                'start_time': st,
                'end_time': et,
                'dur': max(0.0, et - st),
                'category': 'communication',
            }

        def collect_all_reduce(events: list) -> list:
            found = []
            for ev in events or []:
                for ch in ev.get('children', []) or []:
                    nm = (ch.get('name') or '').lower()
                    if 'all_reduce' in nm:
                        found.append(ch)
                found.extend(collect_all_reduce(ev.get('children', [])))
            return found

        # --- Normalize times per step to 0..10 (เพื่อให้ dur เป็นสเกลเดียวกับ runtime) ---
        def collect_times(obj, starts, ends):
            if isinstance(obj, dict):
                st = obj.get('start_time')
                et = obj.get('end_time')
                if isinstance(st, (int, float)):
                    starts.append(st)
                if isinstance(et, (int, float)):
                    ends.append(et)
                for v in obj.values():
                    if isinstance(v, (dict, list)):
                        collect_times(v, starts, ends)
            elif isinstance(obj, list):
                for it in obj:
                    collect_times(it, starts, ends)

        def normalize_step(step_obj):
            starts, ends = [], []
            collect_times(step_obj, starts, ends)
            if not starts or not ends:
                return
            mn = min(starts); mx = max(ends); span = mx - mn
            if span <= 0: return
            scale = 10.0 / span
            def apply(o):
                if isinstance(o, dict):
                    if isinstance(o.get('start_time'), (int, float)):
                        o['start_time'] = (o['start_time'] - mn) * scale
                    if isinstance(o.get('end_time'), (int, float)):
                        o['end_time'] = (o['end_time'] - mn) * scale
                    if 'start_time' in o and 'end_time' in o:
                        o['dur'] = o['end_time'] - o['start_time']
                    for v in o.values():
                        if isinstance(v, (dict, list)):
                            apply(v)
                elif isinstance(o, list):
                    for it in o:
                        apply(it)
            apply(step_obj)

        result = {}

        for step_key, step in tree.items():
            # normalize ก่อน
            normalize_step(step)
            nodes = []
            edges = []
            id_seq = 0
            def next_id(prefix: str) -> str:
                nonlocal id_seq
                id_seq += 1
                return f"{prefix}_{id_seq}"

            comp_nodes = []  # เก็บ (id, ev)

            # เตรียมลิสต์โครงสร้างหลักสำหรับลิงก์ที่ชัดเจน
            forwards_src = step.get('forward', []) or []
            backward_list = step.get('backward', []) or []
            loss = step.get('loss') if isinstance(step.get('loss'), dict) else None
            opt = step.get('optimizer') if isinstance(step.get('optimizer'), dict) else None

            # --- Computation: forward ---
            forward_ids = []
            for ev in forwards_src:
                nid = next_id('comp')
                nodes.append({
                    'id': nid,
                    'label': ev.get('name', 'forward'),
                    'category': 'computation',
                    'lane': 'top',
                    'start_time': ev.get('start_time'),
                    'end_time': ev.get('end_time'),
                    'dur': duration_of(ev),
                })
                comp_nodes.append((nid, ev))
                forward_ids.append(nid)

            # --- Computation: loss ---
            if loss:
                nid = next_id('comp')
                nodes.append({
                    'id': nid,
                    'label': loss.get('name', 'loss'),
                    'category': 'computation',
                    'lane': 'top',
                    'start_time': loss.get('start_time'),
                    'end_time': loss.get('end_time'),
                    'dur': duration_of(loss),
                })
                comp_nodes.append((nid, loss))
                loss_id = nid
            else:
                loss_id = None

            # --- Computation: backward (และสกัด communication ของมัน) ---
            backward_id_to_comm_id = {}
            backward_ids = []
            for ev in backward_list:
                # backward node
                nid = next_id('comp')
                nodes.append({
                    'id': nid,
                    'label': ev.get('name', 'backward'),
                    'category': 'computation',
                    'lane': 'top',
                    'start_time': ev.get('start_time'),
                    'end_time': ev.get('end_time'),
                    'dur': duration_of(ev),
                })
                comp_nodes.append((nid, ev))
                backward_ids.append(nid)

                # group all_reduce children for this backward
                ar_children = collect_all_reduce([ev])
                grouped = group_comm_interval(ar_children)
                if grouped:
                    cid = next_id('comm')
                    nodes.append({
                        'id': cid,
                        'label': 'nccl:all_reduce',
                        'category': 'communication',
                        'lane': 'bottom',
                        **grouped,
                    })
                    # edge: backward -> its all_reduce
                    edges.append({'source': nid, 'target': cid, 'kind': 'backward_to_allreduce'})
                    backward_id_to_comm_id[nid] = cid

            # --- Computation: optimizer ---
            if opt:
                nid = next_id('comp')
                nodes.append({
                    'id': nid,
                    'label': opt.get('name', 'optimizer'),
                    'category': 'computation',
                    'lane': 'top',
                    'start_time': opt.get('start_time'),
                    'end_time': opt.get('end_time'),
                    'dur': duration_of(opt),
                })
                comp_nodes.append((nid, opt))
                optimizer_id = nid
            else:
                optimizer_id = None

            # --- Communication: broadcasts (รวมทั้ง step เป็นก้อนเดียว) ---
            # รวม broadcasts ทั้งหมดใน step (ถ้ามีมากกว่า 1 จะถูกรวมเป็นก้อนเดียว)
            bcast_group = group_comm_interval(step.get('broadcasts', []) or [])
            bcast_id = None
            if bcast_group:
                bcast_id = next_id('comm')
                nodes.append({
                    'id': bcast_id,
                    'label': 'nccl:broadcast',
                    'category': 'communication',
                    'lane': 'bottom',
                    **bcast_group,
                })

            # --- เชื่อมโยงตามกฎที่กำหนด ---
            # 1) broadcast -> forward ตัวแรก
            if bcast_id and forward_ids:
                edges.append({'source': bcast_id, 'target': forward_ids[0], 'kind': 'bcast_to_first_forward'})

            # 2) chain forwards
            for i in range(len(forward_ids)-1):
                edges.append({'source': forward_ids[i], 'target': forward_ids[i+1], 'kind': 'seq'})

            # 3) last forward -> loss (ถ้ามี)
            if loss_id and forward_ids:
                edges.append({'source': forward_ids[-1], 'target': loss_id, 'kind': 'seq'})

            # --- สร้างเส้นสำหรับ communication → computation ถัดไป ---
            # (จะสร้างหลังรู้ลำดับ backward)

            # 4) loss -> backward แรก
            if loss_id and backward_ids:
                edges.append({'source': loss_id, 'target': backward_ids[0], 'kind': 'seq'})

            # ตอนสร้าง backward เราได้ทำ map backward_id_to_comm_id แล้ว
            # เราจะสร้างลิงก์ตามกฎ: backward -> (all_reduce ถ้ามี) -> backward ถัดไป; ถ้าไม่มีถัดไปให้วิ่งไป optimizer
            for i, bid in enumerate(backward_ids):
                comm_id = backward_id_to_comm_id.get(bid)
                next_b = backward_ids[i+1] if i+1 < len(backward_ids) else None
                if comm_id:
                    edges.append({'source': bid, 'target': comm_id, 'kind': 'backward_to_allreduce'})
                    if next_b:
                        edges.append({'source': comm_id, 'target': next_b, 'kind': 'allreduce_to_next_backward'})
                    else:
                        # ไป optimizer ถ้ามี
                        if optimizer_id:
                            edges.append({'source': comm_id, 'target': optimizer_id, 'kind': 'allreduce_to_optimizer'})
                else:
                    if next_b:
                        edges.append({'source': bid, 'target': next_b, 'kind': 'seq'})
                    else:
                        if optimizer_id:
                            edges.append({'source': bid, 'target': optimizer_id, 'kind': 'to_optimizer'})

            # ถ้าไม่มี backward แต่มี loss และ optimizer ให้ต่อ loss -> optimizer
            if loss_id and not backward_ids and optimizer_id:
                edges.append({'source': loss_id, 'target': optimizer_id, 'kind': 'seq'})

            result[step_key] = { 'nodes': nodes, 'edges': edges }

        return self.respond_as_json(result)

    @wrappers.Request.application
    def static_file_route(self, request: werkzeug.Request):
        filename = os.path.basename(request.path)
        extension = os.path.splitext(filename)[1]
        if extension == '.html':
            mimetype = 'text/html'
        elif extension == '.js':
            mimetype = 'application/javascript'
        else:
            mimetype = 'application/octet-stream'
        filepath = os.path.join(os.path.dirname(__file__), 'static', filename)
        try:
            with open(filepath, 'rb') as infile:
                contents = infile.read()
        except IOError:
            raise exceptions.NotFound('404 Not Found')
        return werkzeug.Response(
            contents, content_type=mimetype, headers=CGSDNNAnalysisPlugin.headers
        )

    @staticmethod
    def respond_as_json(obj):
        if hasattr(obj, 'to_dict'):
            obj = obj.to_dict()
        content = json.dumps(obj)
        return werkzeug.Response(content, content_type=CGSDNNAnalysisPlugin.CONTENT_TYPE, headers=CGSDNNAnalysisPlugin.headers)

    @property
    def is_loading(self):
        with self._load_lock:
            return bool(self._load_threads)

    def _monitor_runs(self):
        logger.info('Monitor runs begin')
        touched = set()
        while True:
            try:
                run_dirs = self._get_run_dirs()
                for run_dir in run_dirs:
                    if run_dir not in touched:
                        touched.add(run_dir)
                        logger.info('Find run directory %s', run_dir)
                        t = threading.Thread(target=self._load_run, args=(run_dir,))
                        t.start()
                        with self._load_lock:
                            self._load_threads.append(t)
            except Exception as ex:
                logger.warning('Failed to scan runs. Exception=%s', ex, exc_info=True)
            time.sleep(consts.MONITOR_RUN_REFRESH_INTERNAL_IN_SECONDS)

    def _receive_runs(self):
        while True:
            run: Run = self._queue.get()
            if run is None:
                continue
            logger.info('Add run %s', run.name)
            
            # เพิ่ม run เข้าไปใน runs dictionary
            with self._runs_lock:
                is_new = run.name not in self._runs
                self._runs[run.name] = run
                if is_new:
                    self._runs = OrderedDict(sorted(self._runs.items()))
            
            # โหลดและเก็บ operator trees สำหรับทุก worker
            with self._operator_trees_lock:
                if run.name not in self._operator_trees:
                    self._operator_trees[run.name] = {}
                
                for worker in run.workers:
                    profile = run.get_profile(worker)
                    if profile:
                        tree = profile.get_operator_tree()
                        if tree:
                            self._operator_trees[run.name][worker] = tree
            
            logger.info(f'Loaded operator trees for run {run.name}')

    def _get_run_dirs(self):
        if not io.isdir(self.logdir):
            return
        for root, _, files in io.walk(self.logdir):
            for file in files:
                if utils.is_chrome_trace_file(file):
                    yield root
                    break

    def _load_run(self, run_dir):
        name = self._get_run_name(run_dir)
        try:
            logger.info('Load run %s', name)
            loader = RunLoader(name, run_dir, self._cache)
            run = loader.load()
            logger.info('Run %s loaded', name)
            self._queue.put(run)
        except Exception as ex:
            logger.warning('Failed to load run %s. Exception=%s', name, ex, exc_info=True)

        t = threading.current_thread()
        with self._load_lock:
            try:
                self._load_threads.remove(t)
            except ValueError:
                logger.warning('could not find the thread {}'.format(run_dir))

    def _get_run(self, name) -> Run:
        with self._runs_lock:
            run = self._runs.get(name, None)
        if run is None:
            raise exceptions.NotFound(f'could not find the run for {name}')
        return run

    def _get_run_name(self, run_dir):
        logdir = io.abspath(self.logdir)
        if run_dir == logdir:
            name = io.basename(run_dir)
        else:
            name = io.relpath(run_dir, logdir)
        return name

    def _validate(self, **kwargs):
        for name, v in kwargs.items():
            if v is None:
                raise exceptions.BadRequest(f'Must specify {name} in request url')
                
    def get_all_operator_trees(self):
        """เรียกดูข้อมูล operator trees ทั้งหมดที่มีอยู่"""
        with self._operator_trees_lock:
            return self._operator_trees.copy()
            
    @wrappers.Request.application
    def all_operator_trees_route(self, request: werkzeug.Request):
        """Returns all operator trees data"""
        return self.respond_as_json(self.get_all_operator_trees())



    @wrappers.Request.application
    def communication_timing_route(self, request: werkzeug.Request):
        """
        Processes all cached operator trees and returns the averaged communication
        timing data for each run, without enforcing a common structure.
        """
        all_trees = self.get_all_operator_trees()
        
        collected_durations = {}

        # 1. วนลูปเพื่อรวบรวมข้อมูล (ส่วนนี้เหมือนเดิม)
        for run_name, workers_data in all_trees.items():
            if run_name not in collected_durations:
                collected_durations[run_name] = {}

            for worker_name, steps_data in workers_data.items():
                for step_name, step_content in steps_data.items():
                    # --- ดึงข้อมูล Broadcasts ---
                    broadcasts = step_content.get("broadcasts", [])
                    for i, event in enumerate(broadcasts):
                        key = f"broadcast_{i}"
                        if key not in collected_durations[run_name]:
                            collected_durations[run_name][key] = []
                        duration = event.get("end_time", 0) - event.get("start_time", 0)
                        if duration > 0:
                            collected_durations[run_name][key].append(duration)

                    # --- ดึงข้อมูล All Reduce จาก Backward pass ---
                    backward_events = step_content.get("backward", [])
                    all_reduce_counter = 0
                    
                    def find_all_reduce(events):
                        nonlocal all_reduce_counter
                        for event in events:
                            children = event.get("children", [])
                            for child in children:
                                if child.get("name") == "nccl:all_reduce":
                                    key = f"all_reduce_{all_reduce_counter}"
                                    if key not in collected_durations[run_name]:
                                        collected_durations[run_name][key] = []
                                    dur = child.get("dur", 0)
                                    if dur > 0:
                                        collected_durations[run_name][key].append(dur)
                                    all_reduce_counter += 1
                            find_all_reduce(children)
                    find_all_reduce(backward_events)

        # ### ส่วนที่แก้ไข ###
        # 2. คำนวณค่าเฉลี่ยและสร้างผลลัพธ์สุดท้าย
        # ผลลัพธ์จะเป็น Dictionary ที่มี run_name เป็น key หลัก
        # {
        #   "run_name_1": {"broadcast_0": 0.01, "all_reduce_0": 0.03, ...},
        #   "run_name_2": {"broadcast_0": 0.02, "all_reduce_1": 0.05, ...} // key อาจไม่เหมือนกัน
        # }
        final_data = {}
        for run_name, events in collected_durations.items():
            final_data[run_name] = {}
            for key, durations in events.items():
                if durations:
                    average_duration = sum(durations) / len(durations)
                    final_data[run_name][key] = round(average_duration, 4)
        
        return self.respond_as_json(final_data)