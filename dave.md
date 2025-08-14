# CGS-DNN Analysis: คู่มืออธิบายโค้ดและ Flow ของโปรเจค

## ภาพรวมของโปรเจค (Project Overview)

CGS-DNN Analysis (Computational Graph System for Deep Neural Network Analysis) เป็น TensorBoard plugin ที่ออกแบบมาเพื่อวิเคราะห์และแสดงผล profiling data จาก PyTorch โดยเฉพาะอย่างยิ่งสำหรับ distributed training ที่มีการสื่อสารระหว่าง nodes/GPUs

### วัตถุประสงค์หลัก:
1. แสดงผล operator tree และ execution timeline
2. วิเคราะห์ communication patterns (broadcast, all-reduce)
3. เปรียบเทียบ performance ระหว่าง algorithms ต่างๆ
4. แสดง DAG (Directed Acyclic Graph) ของ computation และ communication

## โครงสร้างโปรเจค (Project Structure)

```
cgs_dnn_analysis/
├── __init__.py          # Entry point, version info
├── plugin.py            # TensorBoard plugin หลัก
├── run.py               # Data models สำหรับ runs และ profiles
├── consts.py            # Constants และ configurations
├── debug_operations.py  # Debug utilities สำหรับ operations
├── multiprocessing.py   # Multiprocessing utilities
├── utils.py             # General utilities
├── profiler/            # โมดูลสำหรับ parse profiling data
├── io/                  # File I/O และ caching
└── static/              # Frontend (HTML/JS)
```

## Flow การทำงานหลัก (Main Workflow)

### 1. การโหลดข้อมูล (Data Loading)

```mermaid
graph TD
    A[TensorBoard เริ่มต้น] --> B[CGSDNNAnalysisPlugin.__init__]
    B --> C[Monitor Runs Thread]
    C --> D[สแกนหา trace files]
    D --> E[RunLoader โหลด traces]
    E --> F[Parse JSON traces]
    F --> G[สร้าง operator trees]
    G --> H[เก็บใน cache]
```

#### 1.1 Plugin Initialization
- [`plugin.py:__init__()`](cgs_dnn_analysis/plugin.py:47) สร้าง plugin instance
- เริ่ม 2 background threads:
  - `_monitor_runs`: สแกนหา trace files ใหม่
  - `_receive_runs`: รับและประมวลผล runs

#### 1.2 Trace File Discovery
- [`plugin.py:_monitor_runs()`](cgs_dnn_analysis/plugin.py:564) สแกน logdir ทุก 10 วินาที
- ค้นหาไฟล์ที่ match pattern: `*.pt.trace.json` หรือ `*.pt.trace.json.gz`
- สร้าง thread ใหม่สำหรับโหลดแต่ละ run

#### 1.3 Data Loading และ Parsing
- [`profiler/loader.py:RunLoader`](cgs_dnn_analysis/profiler/loader.py:23) โหลด trace files
- [`profiler/data.py:RunProfileData`](cgs_dnn_analysis/profiler/data.py:23) parse JSON traces
- สร้าง operator tree จาก trace events

### 2. การประมวลผล Operator Trees

```mermaid
graph LR
    A[Raw Trace Events] --> B[EventParser]
    B --> C[OpTreeBuilder]
    C --> D[Operator Nodes]
    D --> E[Step-based Structure]
```

#### 2.1 Event Parsing
- [`profiler/event_parser.py:EventParser`](cgs_dnn_analysis/profiler/event_parser.py:111) แปลง events เป็น nodes
- จัดกลุ่ม events ตาม thread ID (TID)
- สร้าง hierarchy ของ operations

#### 2.2 Tree Building
- [`profiler/op_tree.py:OpTreeBuilder`](cgs_dnn_analysis/profiler/op_tree.py:18) สร้าง operator tree
- แยก operations ตาม ProfilerStep
- จัดการ forward/backward relationships

#### 2.3 Data Collection
- [`run.py:StepDataCollector`](cgs_dnn_analysis/run.py:41) รวบรวม operations ตาม step
- แยกประเภท: forward, loss, backward, optimizer
- จับคู่ communication operations (broadcast, all-reduce)

### 3. API Endpoints

Plugin ให้บริการผ่าน HTTP endpoints:

#### 3.1 `/runs` - รายชื่อ runs
```python
# plugin.py:runs_route()
{
    "runs": ["run1", "run2", ...],
    "loading": false
}
```

#### 3.2 `/workers` - รายชื่อ workers ใน run
```python
# plugin.py:workers_route()
["worker1", "worker2", ...]
```

#### 3.3 `/runtime` - Operator tree data
```python
# plugin.py:runtime_route()
{
    "1": {  # step number
        "forward": {...},
        "loss": {...},
        "backward": [...],
        "optimizer": {...},
        "broadcasts": [...]
    }
}
```

#### 3.4 `/dag` - DAG visualization data
```python
# plugin.py:dag_route()
{
    "1": {  # step number
        "nodes": [
            {
                "id": "comp_1",
                "label": "Conv2d",
                "category": "computation",
                "lane": "top",
                "dur": 2.5
            }
        ],
        "edges": [
            {
                "source": "comp_1",
                "target": "comp_2",
                "kind": "seq"
            }
        ]
    }
}
```

#### 3.5 `/communication_timing` - เปรียบเทียบ timing
```python
# plugin.py:communication_timing_route()
{
    "Ring": {
        "broadcast_0": 0.0123,
        "all_reduce_0": 0.0456
    },
    "Tree": {...}
}
```

### 4. Frontend Visualization

Frontend ใช้ D3.js สำหรับ visualization:

#### 4.1 Views ที่มี:
1. **Runtime View**: Timeline แสดง operations บน 2 lanes
   - Computation lane: forward, loss, backward, optimizer
   - Communication lane: broadcast, all-reduce

2. **DAG View**: Graph แสดงความสัมพันธ์
   - Nodes: operations (ขนาดตาม duration)
   - Edges: dependencies และ data flow

3. **Communication Timing Table**: เปรียบเทียบ algorithms

#### 4.2 Features:
- Zoom และ pan สำหรับดูรายละเอียด
- Tooltips แสดงข้อมูล timing
- Responsive design
- Label visibility ตาม zoom level

## การประมวลผล Operations (Operations Processing)

### 1. การจัดกลุ่ม Operations

[`debug_operations.py:StepDataCollector`](cgs_dnn_analysis/debug_operations.py:38) จัดการ:

```python
# แยกตาม operation type
operations = {
    'forward': 'nn.Module: DistributedDataParallel_0',
    'loss': 'aten::cross_entropy_loss',
    'backward': 'nn.Module: DistributedDataParallel_0.backward',
    'optimizer': 'Optimizer.step#SGD.step',
    'broadcast': 'nccl:broadcast',
    'allreduce': 'nccl:all_reduce'
}
```

### 2. การจับคู่ Communication Operations

Communication operations ถูกจับคู่กับ computation ผ่าน `external_id`:

```python
# จับคู่ broadcast กับ forward operation
if current.get('external_id') == external_id:
    step_data['broadcasts'].append(broadcast_op)

# จับคู่ all_reduce กับ backward operation
if node.get('external_id') == external_id:
    # แทนที่ placeholder ด้วยข้อมูลจริง
    node.update(all_reduce_data)
```

### 3. การ Normalize เวลา

เวลาถูก normalize เป็น scale 0-10 สำหรับแต่ละ step:

```python
# plugin.py:normalize_within_step()
scale = 10.0 / (max_time - min_time)
normalized_time = (time - min_time) * scale
```

## Data Models

### 1. Run และ RunProfile

```python
class Run:
    - name: ชื่อ run
    - run_dir: directory path
    - profiles: dict ของ worker profiles

class RunProfile:
    - worker: ชื่อ worker
    - span: time span
    - tid2tree: operator trees ตาม thread ID
```

### 2. Operator Nodes

```python
class OperatorNode:
    - name: ชื่อ operation
    - type: ประเภท (e.g., 'aten::conv2d')
    - children: list ของ child operations
    - start_time: เวลาเริ่ม
    - end_time: เวลาสิ้นสุด
    - external_id: ID สำหรับจับคู่ communication
```

## การ Cache และ Performance

### 1. Caching Strategy
- Operator trees ถูก cache ใน memory (`_operator_trees`)
- Communication timing ถูก cache (`commTimingCache`)
- ลด repeated parsing และ computation

### 2. Multiprocessing
- ใช้ spawn method สำหรับ multiprocessing
- แต่ละ run โหลดใน thread แยก
- ไม่ block UI ระหว่างโหลด

## การติดตั้งและใช้งาน

### 1. Installation
```bash
pip install -e .
```

### 2. การใช้งานกับ TensorBoard
```bash
tensorboard --logdir=./log --load_fast=false
```

### 3. การสร้าง Trace Files
ใช้ PyTorch Profiler:
```python
with torch.profiler.profile(
    activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
    on_trace_ready=torch.profiler.tensorboard_trace_handler('./log')
) as prof:
    # training code
    prof.step()
```

## ข้อควรระวังและ Best Practices

1. **Memory Usage**: Trace files ขนาดใหญ่อาจใช้ memory มาก
2. **File Naming**: ต้องตาม pattern `*.pt.trace.json`
3. **Step Synchronization**: ต้องมี ProfilerStep markers
4. **Communication Tracking**: ต้องมี external_id สำหรับจับคู่

## การ Debug และ Troubleshooting

### 1. Debug Operations
ใช้ [`debug_operations.py`](cgs_dnn_analysis/debug_operations.py:456):
```python
debug_operations_data(input_file, output_file)
```

### 2. Log Levels
ตั้งค่าผ่าน environment variable:
```bash
export TORCH_PROFILER_LOG_LEVEL=DEBUG
```

### 3. Common Issues
- **ไม่เจอ traces**: ตรวจสอบ file pattern และ path
- **Memory overflow**: ลด trace duration หรือ sampling rate
- **Missing operations**: ตรวจสอบ profiler configuration

## สรุป

CGS-DNN Analysis เป็นเครื่องมือที่ powerful สำหรับวิเคราะห์ distributed DNN training โดย:
- แสดง execution timeline แบบ interactive
- วิเคราะห์ communication overhead
- เปรียบเทียบ algorithms ต่างๆ
- ช่วยระบุ bottlenecks และ optimization opportunities

Plugin นี้ integrate กับ TensorBoard อย่างลงตัว และให้ insights ที่ลึกซึ้งเกี่ยวกับ training performance โดยเฉพาะในระบบ multi-GPU/multi-node.
