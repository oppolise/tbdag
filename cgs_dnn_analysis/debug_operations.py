import json
import logging
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass
from collections import defaultdict

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class OperatorNode:
    name: str
    type: str
    children: list
    start_time: float
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OperatorNode':
        return cls(
            name=data.get('name', ''),
            type=data.get('type', ''),
            children=data.get('children', []),
            start_time=data.get('start_time', 0.0)
        )
        
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'type': self.type,
            'children': self.children,
            'start_time': self.start_time
        }

class StepDataCollector:
    def __init__(self):
        self.steps_data = defaultdict(dict)
        
    def extract_step_number(self, name: str) -> int:
        """Extract step number from ProfilerStep name"""
        import re
        match = re.search(r'ProfilerStep#(\d+)', name)
        return int(match.group(1)) if match else None
    
    def is_operation(self, name: str, op_type: str) -> bool:
        """Check if node name matches operation type"""
        operations = {
            'broadcast': 'nccl:broadcast',
            'forward': 'nn.Module: DistributedDataParallel_0',
            'loss': 'aten::cross_entropy_loss',
            'backward': 'nn.Module: DistributedDataParallel_0.backward',
            'allreduce': 'nccl:all_reduce',
            'optimizer': 'Optimizer.step#SGD.step'
        }
        return name == operations.get(op_type, '')
    
    def find_operation_in_node(self, node: Dict[str, Any], op_type: str) -> Dict[str, Any]:
        """Find specific operation in a node or its children"""
        if not node:
            return None
            
        name = node.get('name', '')
        if self.is_operation(name, op_type):
            return node
            
        for child in node.get('children', []):
            result = self.find_operation_in_node(child, op_type)
            if result:
                return result
        return None
    
    def process_main_thread(self, thread_data: Dict[str, Any]):
        """Process main thread that contains steps with forward and loss"""
        if not thread_data.get('children'):
            return
            
        for child in thread_data['children']:
            if child.get('type') == 'ProfilerStep':
                step_num = self.extract_step_number(child.get('name', ''))
                if step_num is not None:
                    logger.debug(f"Processing step {step_num} in main thread")
                    
                    # Look for forward and loss in this step
                    forward_op = self.find_operation_in_node(child, 'forward')
                    if forward_op:
                        self.steps_data[step_num]['forward'] = forward_op
                        
                    loss_op = self.find_operation_in_node(child, 'loss')
                    if loss_op:
                        self.steps_data[step_num]['loss'] = loss_op
    
    def process_backward_thread(self, node: Dict[str, Any], current_step: int):
        """Process backward or optimizer operation node"""
        if not node:
            return
            
        # Check if this node is backward operation
        if self.is_operation(node.get('name', ''), 'backward'):
            self.steps_data[current_step]['backward'] = node
            return
            
        # Check if this node is optimizer operation
        if self.is_operation(node.get('name', ''), 'optimizer'):
            self.steps_data[current_step]['optimizer'] = node
            return
            
    def process_communication_operations(self, thread_data: Dict[str, Any]):
        """Process communication operations (broadcast and all_reduce)"""
        if not thread_data.get('children'):
            return
            
        for child in thread_data.get('children', []):
            name = child.get('name', '')
            external_id = child.get('external_id')
            
            if name == 'nccl:broadcast':
                # สำหรับ broadcast ตรวจสอบทุก step
                for step_num, step_data in self.steps_data.items():
                    forward_data = step_data.get('forward', {})
                    if not forward_data:
                        continue
                        
                    # เช็คทุก child ใน forward operation
                    queue = [forward_data]  # ใช้ queue สำหรับ traverse
                    while queue:
                        current = queue.pop(0)
                        if current.get('external_id') == external_id:
                            # พบ external_id ที่ตรงกัน ให้เก็บใน step ที่พบ
                            # สร้าง list สำหรับเก็บ broadcasts ถ้ายังไม่มี
                            if 'broadcasts' not in self.steps_data[step_num]:
                                self.steps_data[step_num]['broadcasts'] = []
                            self.steps_data[step_num]['broadcasts'].append(child)
                            
                        # เพิ่ม children เข้า queue
                        queue.extend(current.get('children', []))
                        
            elif name == 'nccl:all_reduce':
                # สำหรับ all_reduce ตรวจสอบทุก step
                found_match = False
                for step_num, step_data in self.steps_data.items():
                    backward_data = step_data.get('backward', {})
                    if not backward_data:
                        continue
                        
                    # เช็คทุก child ใน backward operation
                    queue = [backward_data]  # ใช้ queue สำหรับ traverse
                    while queue and not found_match:
                        current = queue.pop(0)
                        if current.get('external_id') == external_id and current.get('name') == 'nccl:all_reduce':
                            # ถ้าเจอ nccl:all_reduce ที่มี external_id ตรงกัน ให้แทนที่เลย
                            current.update(child)  # แทนที่เฉพาะข้อมูลของ operation นั้น
                            found_match = True
                            break
                            
                        # เพิ่ม children เข้า queue
                        queue.extend(current.get('children', []))
                        
                    if found_match:
                        break  # ออกจาก loop ของ step ถ้าเจอแล้ว
                
    def get_current_step(self, thread_data: Dict[str, Any]) -> int:
        """Get current step number from ProfilerStep node"""
        if not thread_data.get('children'):
            return None
            
        for child in thread_data['children']:
            if child.get('type') == 'ProfilerStep':
                return self.extract_step_number(child.get('name', ''))
        return None

def prepare_backward_data(backward_data: Dict[str, Any]) -> Dict[str, Any]:
    """เตรียมข้อมูล backward โดยดึง children ชั้นที่สองขึ้นมา"""
    if not backward_data or 'children' not in backward_data:
        return backward_data
        
    # เข้าถึง children ชั้นแรก
    if len(backward_data.get('children', [])) == 0:
        return backward_data
        
    first_child = backward_data['children'][0]
    second_level_children = first_child.get('children', [])
    
    # ถ้าไม่มี children ชั้นที่สอง ให้คืนค่าเดิม
    if not second_level_children:
        return backward_data
        
    # สร้างข้อมูลใหม่โดยใช้ children ชั้นที่สอง
    prepared_data = backward_data.copy()
    prepared_data = second_level_children
    return prepared_data

def filter_backward_data(layers: list) -> list:
    """กรองข้อมูล backward operation สำหรับแต่ละ layer โดยหา nccl:all_reduce ทุกตัวใน children (recursive) ถ้าเจอให้นำมาใส่ใน children เป็น list ถ้าไม่เจอเลย children เป็น []"""
    def find_all_reduces(node: dict):
        found = []
        for child in node.get('children', []):
            if child.get('name') == 'nccl:all_reduce':
                found.append(child)
            found.extend(find_all_reduces(child))
        return found

    filtered = []
    for node in layers:
        all_reduces = find_all_reduces(node)
        if all_reduces:
            node['children'] = all_reduces
        else:
            node['children'] = []
        filtered.append(node)
    return filtered

def load_and_sort_tid2tree(file_path: str) -> List[Tuple[int, Dict[str, Any]]]:
    """Load tid2tree from file and sort by start_time"""
    try:
        with open(file_path, 'r') as f:
            tid2tree_dict = json.load(f)
            
        # Convert to list of tuples (tid, data) and sort by start_time
        tid_data_pairs = [(int(tid), data) for tid, data in tid2tree_dict.items()]
        return sorted(tid_data_pairs, key=lambda x: x[1].get('start_time', 0))
        
    except Exception as e:
        logger.error(f"Error loading tid2tree: {str(e)}")
        return []

def clean_backward_name(name: str) -> str:
    """
    กรองเฉพาะชื่อที่มี .backward ติดอยู่ และลบ .backward ออก
    คืนค่า None หากไม่มี .backward
    """
    if '.backward' in name:
        # ลบ .backward ออก
        cleaned_name = name.replace('.backward', '')
        # ลบ prefix autograd::engine::evaluate_function: ถ้ามี
        if cleaned_name.startswith('autograd::engine::evaluate_function: '):
            cleaned_name = cleaned_name.replace('autograd::engine::evaluate_function: ', '')
        # ลบ prefix nn.Module: ถ้ามี เพื่อให้เหลือแค่ชื่อ layer
        if cleaned_name.startswith('nn.Module: '):
            cleaned_name = cleaned_name.replace('nn.Module: ', '')
        return cleaned_name
    return None

def find_forward_operation_by_name(forward_root, target_name):
    """
    หา forward operation จากชื่อ target_name ใน forward tree
    คืนค่า node ที่ตรงกัน พร้อมกับข้อมูลที่ถูกต้อง
    """
    def recursive_search(node, target):
        if not node:
            return None
            
        node_name = node.get('name', '')
        # ลบ prefix nn.Module: เพื่อเปรียบเทียบ
        if node_name.startswith('nn.Module: '):
            clean_node_name = node_name.replace('nn.Module: ', '')
        else:
            clean_node_name = node_name
            
        if clean_node_name == target:
            return node
            
        # ค้นหาใน children
        for child in node.get('children', []):
            result = recursive_search(child, target)
            if result:
                return result
        return None
    
    return recursive_search(forward_root, target_name)

def prepare_forward_and_loss_data(step_data: dict):
    """
    - forward: ดึง forward operations จากชื่อที่ได้จาก backward_nodes
    - loss: set children = []
    """
    
    # จัดการ forward
    if 'forward' in step_data and 'backward' in step_data:
        forward_root = step_data['forward']
        backward_nodes = step_data['backward']
        
        # backward_nodes อาจเป็น node เดียวหรือ list
        if not isinstance(backward_nodes, list):
            backward_nodes = [backward_nodes]
        
        # reverse backward เพื่อให้ลำดับตรงกับ forward
        backward_nodes = list(reversed(backward_nodes))
        
        # กรองและ clean ชื่อจาก backward_nodes
        valid_backward_names = []
        for bwd_node in backward_nodes:
            cleaned_name = clean_backward_name(bwd_node.get('name', ''))
            if cleaned_name:  # เฉพาะที่มี .backward เท่านั้น
                valid_backward_names.append(cleaned_name)
        
        # ดึง forward operations จากชื่อที่ clean แล้ว
        mapped_forward_ops = []
        for target_name in valid_backward_names:
            # หา forward operation ที่ตรงกับชื่อ
            found_forward = find_forward_operation_by_name(forward_root, target_name)
            
            if found_forward:
                # ใช้ข้อมูลจริงที่ดึงได้ (รวม start_time ที่ถูกต้อง)
                forward_op = found_forward.copy()
                forward_op['children'] = []  # เคลียร์ children
                mapped_forward_ops.append(forward_op)
            else:
                # ถ้าไม่เจอ ให้ใช้ข้อมูลจาก root แต่เปลี่ยนชื่อ
                fallback_forward = forward_root.copy() if not isinstance(forward_root, list) else forward_root[0].copy()
                fallback_forward['name'] = f'nn.Module: {target_name}'
                fallback_forward['children'] = []
                mapped_forward_ops.append(fallback_forward)
        
        # อัพเดท forward ใน step_data
        if len(mapped_forward_ops) == 1:
            step_data['forward'] = mapped_forward_ops[0]
        elif len(mapped_forward_ops) > 1:
            step_data['forward'] = mapped_forward_ops
        # ถ้าไม่มี valid backward names เลย ให้คงเดิม
    
    # จัดการ loss
    if 'loss' in step_data:
        loss_node = step_data['loss']
        if isinstance(loss_node, list):
            for l in loss_node:
                l['children'] = []
        else:
            loss_node['children'] = []
        step_data['loss'] = loss_node

def process_tid2tree(file_path: str):
    """Process tid2tree file and collect operations data"""
    try:
        # Load and sort tid2tree
        sorted_tid_data = load_and_sort_tid2tree(file_path)
        if not sorted_tid_data:
            logger.error("No data loaded from tid2tree file")
            return None
            
        collector = StepDataCollector()
        current_step = None
        
        # Process threads in order
        first_thread = True  # สถานะสำหรับ forward/loss
        backward_phase = False  # สถานะสำหรับ backward/optimizer
        communication_phase = False  # สถานะสำหรับ communication operations
        current_step = None
        
        for tid, thread_data in sorted_tid_data:
            logger.debug(f"Processing TID: {tid}")
            
            if thread_data.get('name') == 'CallTreeRoot':
                if first_thread:
                    # Process main thread (forward/loss)
                    logger.debug(f"Processing main thread with forward/loss (TID: {tid})")
                    collector.process_main_thread(thread_data)
                    first_thread = False
                    backward_phase = True  # เริ่มเฟส backward ได้
                
                elif backward_phase:
                    # Process backward/optimizer operations
                    logger.debug(f"Processing backward/optimizer thread (TID: {tid})")
                    children = thread_data.get('children', [])
                    has_backward_or_optimizer = False
                    
                    for i, child in enumerate(children):
                        if child.get('type') == 'ProfilerStep':
                            current_step = collector.extract_step_number(child.get('name', ''))
                            continue
                            
                        # Check for backward or optimizer operations
                        name = child.get('name', '')
                        if name == 'nn.Module: DistributedDataParallel_0.backward' or 'Optimizer.step#SGD.step' in name:
                            has_backward_or_optimizer = True
                            collector.process_backward_thread(child, current_step)
                    
                    # If this thread didn't have backward/optimizer, move to communication phase
                    if has_backward_or_optimizer:
                        backward_phase = False
                        communication_phase = True
                
                elif communication_phase:
                    # Process communication operations
                    logger.debug(f"Processing communication thread (TID: {tid})")
                    collector.process_communication_operations(thread_data)

        # กรองข้อมูลหลังจากเก็บข้อมูลครบ
        logger.debug("Preparing and filtering collected data...")
        for step_data in collector.steps_data.values():
            if 'backward' in step_data:
                # เตรียมข้อมูล backward โดยดึง children ชั้นที่สองขึ้นมา
                prepared_backward = prepare_backward_data(step_data['backward'])
                # กรองข้อมูล backward (prepared_backward เป็น list)
                step_data['backward'] = filter_backward_data(prepared_backward)
            # จัดการ forward/loss ตามที่ต้องการ
            prepare_forward_and_loss_data(step_data)
            # ตัดข้อมูลและ sort
            trim_and_sort_operations(step_data)
            
        return dict(collector.steps_data)
        
    except Exception as e:
        logger.error(f"Error processing tid2tree: {str(e)}", exc_info=True)
        return None

def trim_and_sort_operations(step_data: dict):
    """
    ตัดข้อมูลแต่ละ node ให้เหลือ field ตามประเภท
    - backward: name, start_time, end_time, type, children
    - อื่นๆ: name, start_time, end_time, type
    """
    def trim_node(node, keep_children=False):
        base = {
            'name': node.get('name', ''),
            'start_time': node.get('start_time', 0),
            'end_time': node.get('end_time', 0),
        }
        if keep_children:
            base['children'] = node.get('children', [])
        return base
    
    for key in ['forward', 'loss', 'backward', 'broadcasts', 'optimizer']:
        if key in step_data:
            nodes = step_data[key]
            if key == 'backward':
                if isinstance(nodes, list):
                    trimmed = [trim_node(n, keep_children=True) for n in nodes]
                    trimmed.sort(key=lambda x: x['start_time'])
                    step_data[key] = trimmed
                else:
                    step_data[key] = trim_node(nodes, keep_children=True)
            else:
                if isinstance(nodes, list):
                    trimmed = [trim_node(n) for n in nodes]
                    trimmed.sort(key=lambda x: x['start_time'])
                    step_data[key] = trimmed
                else:
                    step_data[key] = trim_node(nodes)

def save_operations_data(data: Dict[str, Any], output_path: str):
    """Save operations data to a JSON file"""
    try:
        # Convert step numbers from int to str for JSON serialization
        formatted_data = {str(step): ops for step, ops in data.items()}
        
        with open(output_path, 'w') as f:
            json.dump(formatted_data, f, indent=2)
        logger.info(f"Successfully saved operations data to {output_path}")
        
    except Exception as e:
        logger.error(f"Error saving operations data: {str(e)}")

def debug_operations_data(input_path: str, output_path: str = None):
    """Debug function to show collected operations data and optionally save to file"""
    result = process_tid2tree(input_path)
    if not result:
        logger.error("No results found")
        return
        
    # Log the results
    logger.info("\nCollected Operations Data:")
    for step_num in sorted(result.keys()):
        logger.info(f"\nStep {step_num}:")
        operations = result[step_num]
        for op_type, op_data in operations.items():
            logger.info(f"  Found {op_type} operation")
            logger.debug(f"  Details: {json.dumps(op_data, indent=2)}")
    
    # Save to file if output path is provided
    if output_path:
        save_operations_data(result, output_path)

if __name__ == "__main__":
    import sys
    import os
    
    input_file = '/home/oppolise/Dev/ProjectDag/test-tb/d_plugin/tid2tree.json'
    # Create output filename based on input filename
    output_file = os.path.join(
        os.path.dirname(input_file),
        'operations_' + os.path.basename(input_file)
    )
    debug_operations_data(input_file, output_file)