# cgs_dnn_analysis/run.py
import logging
from typing import Any, Dict, List, Optional
from collections import defaultdict
import re

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OperatorNode:
    def __init__(self,
                 name: str,
                 type: str,
                 children: List[Any],
                 start_time: float,
                 end_time: float = 0.0,
                 external_id: Any = None):
        self.name = name
        self.type = type
        self.children = children
        self.start_time = start_time
        self.end_time = end_time
        self.external_id = external_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'type': self.type,
            'children': [c.to_dict() if hasattr(c, 'to_dict') else c for c in self.children],
            'start_time': self.start_time,
            'end_time': self.end_time,
            'external_id': self.external_id,
        }


class StepDataCollector:
    def __init__(self):
        self.steps_data: Dict[int, Dict[str, Any]] = defaultdict(dict)

    def extract_step_number(self, name: str) -> Optional[int]:
        m = re.search(r'ProfilerStep#(\d+)', name)
        return int(m.group(1)) if m else None

    def is_operation(self, name: str, op_type: str) -> bool:
        ops = {
            'forward': 'nn.Module: DistributedDataParallel_0',
            'loss': 'aten::cross_entropy_loss',
            'backward': 'nn.Module: DistributedDataParallel_0.backward',
            'optimizer': 'Optimizer.step#SGD.step',
            'broadcast': 'nccl:broadcast',
            'allreduce': 'nccl:all_reduce',
        }
        return name == ops.get(op_type, '')

    def find_operation(self, node: Dict[str, Any], op_type: str) -> Optional[Dict[str, Any]]:
        if not node:
            return None
        if self.is_operation(node.get('name', ''), op_type):
            return node
        for c in node.get('children', []):
            res = self.find_operation(c, op_type)
            if res:
                return res
        return None

    def process_main_thread(self, tree: Dict[str, Any]):
        for c in tree.get('children', []):
            if c.get('type') != 'ProfilerStep':
                continue
            step = self.extract_step_number(c.get('name', ''))
            if step is None:
                continue
            logger.debug(f"[Main] step {step}")
            fwd = self.find_operation(c, 'forward')
            if fwd:
                self.steps_data[step]['forward'] = fwd
            loss = self.find_operation(c, 'loss')
            if loss:
                self.steps_data[step]['loss'] = loss

    def process_backward_and_optimizer(self, tree: Dict[str, Any]):
        step = None
        for c in tree.get('children', []):
            if c.get('type') == 'ProfilerStep':
                step = self.extract_step_number(c.get('name', ''))
                continue
            name = c.get('name', '')
            if step is not None and self.is_operation(name, 'backward'):
                self.steps_data[step]['backward'] = c
            elif step is not None and self.is_operation(name, 'optimizer'):
                self.steps_data[step]['optimizer'] = c

    def process_communication(self, tree: Dict[str, Any]):
        for c in tree.get('children', []):
            name = c.get('name', '')
            ext = c.get('external_id')
            if name == 'nccl:broadcast':
                for step, data in self.steps_data.items():
                    fwd = data.get('forward')
                    if not fwd:
                        continue
                    queue = [fwd]
                    while queue:
                        curr = queue.pop(0)
                        if curr.get('external_id') == ext:
                            data.setdefault('broadcasts', []).append(c)
                        queue.extend(curr.get('children', []))
            elif name == 'nccl:all_reduce':
                for data in self.steps_data.values():
                    bwd = data.get('backward')
                    if not bwd:
                        continue
                    queue = [bwd]
                    updated = False
                    while queue and not updated:
                        curr = queue.pop(0)
                        if curr.get('external_id') == ext and curr.get('name') == 'nccl:all_reduce':
                            curr.update(c)
                            updated = True
                        queue.extend(curr.get('children', []))


def prepare_backward_data(bwd: Any) -> Any:
    if not bwd or 'children' not in bwd:
        return bwd
    ch = bwd['children']
    if not ch:
        return bwd
    second = ch[0].get('children', [])
    return second if second else bwd


def filter_backward_data(layers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def find_all(n: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for c in n.get('children', []):
            if c.get('name') == 'nccl:all_reduce':
                out.append(c)
            out.extend(find_all(c))
        return out

    res = []
    for node in layers:
        found = find_all(node)
        node['children'] = found if found else []
        res.append(node)
    return res


def prepare_forward_and_loss_data(step: Dict[str, Any]):
    def clean_backward_name(name: str) -> Optional[str]:
        if not name.endswith('.backward'):
            return None
        return name.rsplit('.backward', 1)[0]

    def find_forward_by_name(root: Dict[str, Any], target: str) -> Optional[Dict[str, Any]]:
        if root.get('name', '').endswith(target):
            return root
        for c in root.get('children', []):
            res = find_forward_by_name(c, target)
            if res:
                return res
        return None

    if 'forward' in step and 'backward' in step:
        fwd = step['forward']
        bwd = step['backward']
        fwd_list = fwd if isinstance(fwd, list) else [fwd]
        bwd_list = bwd if isinstance(bwd, list) else [bwd]

        forward_root = fwd_list[0]
        new_fwd = []
        for b in bwd_list:
            cleaned = clean_backward_name(b.get('name', ''))
            if not cleaned:
                continue
            match = find_forward_by_name(forward_root, cleaned)
            if match:
                node = {
                    'name': match['name'],
                    'start_time': match['start_time'],
                    'end_time': match.get('end_time', match['start_time']),
                    'children': []
                }
                new_fwd.append(node)
        step['forward'] = new_fwd[0] if len(new_fwd) == 1 else new_fwd

    if 'loss' in step:
        L = step['loss']
        if isinstance(L, list):
            for x in L:
                x['children'] = []
        else:
            L['children'] = []
        step['loss'] = L


def trim_and_sort_operations(step: Dict[str, Any]):
    def _compute_category(name: str) -> str:
        if isinstance(name, str) and name.startswith('nccl:'):
            return 'communication'
        return 'computation'

    def trim_recursive(n: Dict[str, Any], keep: bool = False) -> Dict[str, Any]:
        name = n.get('name', '')
        start_us = n.get('start_time', 0)
        end_us = n.get('end_time', start_us)
        start_ms = start_us / 1000.0
        end_ms = end_us / 1000.0
        out: Dict[str, Any] = {
            'name': name,
            'start_time': start_ms,
            'end_time': end_ms,
            'dur': end_ms - start_ms,
            'category': _compute_category(name),
        }
        if keep:
            children = n.get('children', [])
            if isinstance(children, list):
                out['children'] = [trim_recursive(ch, keep=False) for ch in children]
        return out

    for key in ['forward', 'loss', 'backward', 'broadcasts', 'optimizer']:
        if key in step:
            nodes = step[key]
            if isinstance(nodes, list):
                items = [trim_recursive(x, keep=(key=='backward')) for x in nodes]
                items.sort(key=lambda x: x['start_time'])
                step[key] = items
            else:
                step[key] = trim_recursive(nodes, keep=(key=='backward'))


class Run:
    def __init__(self, name: str, run_dir: str):
        self.name = name
        self.run_dir = run_dir
        self.profiles: Dict[str, 'RunProfile'] = {}

    @property
    def workers(self) -> List[str]:
        return sorted(self.profiles.keys())

    def add_profile(self, p: 'RunProfile') -> None:
        self.profiles[p.worker] = p

    def get_profile(self, w: str) -> Optional['RunProfile']:
        if w is None:
            raise ValueError("worker is mandatory")
        return self.profiles.get(w)


class RunProfile:
    def __init__(self, worker: str, span: Any):
        self.worker = worker
        self.span = span
        self.tid2tree: Dict[int, OperatorNode] = {}

    def get_operator_tree(self) -> Optional[Dict[int, Any]]:
        if not self.tid2tree:
            logger.warning(f"tid2tree is empty for {self.worker}")
            return None

        items = sorted(
            [(tid, node.to_dict()) for tid, node in self.tid2tree.items()],
            key=lambda x: x[1].get('start_time', 0)
        )
        collector = StepDataCollector()
        first_main, in_bwd, in_comm = True, False, False

        for tid, tree in items:
            if tree.get('name') == 'CallTreeRoot':
                if first_main:
                    logger.debug(f"[{tid}] main thread")
                    collector.process_main_thread(tree)
                    first_main, in_bwd = False, True
                elif in_bwd:
                    logger.debug(f"[{tid}] backward thread")
                    collector.process_backward_and_optimizer(tree)
                    in_bwd, in_comm = False, True
                elif in_comm:
                    logger.debug(f"[{tid}] comm thread")
                    collector.process_communication(tree)

        for sd in collector.steps_data.values():
            if 'backward' in sd:
                sd['backward'] = filter_backward_data(prepare_backward_data(sd['backward']))
            prepare_forward_and_loss_data(sd)
            trim_and_sort_operations(sd)
        result: Dict[int, Dict[str, Any]] = {}
        for step_num, sd in collector.steps_data.items():
            ordered: Dict[str, Any] = {}
            if 'broadcasts' in sd:
                ordered['broadcasts'] = sd['broadcasts']
            for key in ['forward', 'loss', 'backward', 'optimizer']:
                if key in sd:
                    ordered[key] = sd[key]
            result[step_num] = ordered

        return result
