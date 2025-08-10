import re
import logging
from collections import defaultdict
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

def extract_step_number(name: str) -> Optional[int]:
    """Extract step number from ProfilerStep name"""
    match = re.search(r'ProfilerStep#(\d+)', name)
    return int(match.group(1)) if match else None

def is_ddp_forward(name: str) -> bool:
    """Check if node is DistributedDataParallel forward"""
    return name == 'nn.Module: DistributedDataParallel_0'

def is_ddp_backward(name: str) -> bool:
    """Check if node is DistributedDataParallel backward"""
    return name == 'nn.Module: DistributedDataParallel_0.backward'

def is_loss(name: str) -> bool:
    """Check if node is cross entropy loss"""
    return name == 'aten::cross_entropy_loss'

def is_optimizer(name: str) -> bool:
    """Check if node is optimizer step"""
    return name == 'Optimizer.step#SGD.step'

def find_operation_in_tree(node: Dict[str, Any], operation_type: str) -> Optional[Dict[str, Any]]:
    """Recursively search for specific operation in the tree"""
    if not node:
        return None
        
    name = node.get('name', '')
    if operation_type == 'forward' and is_ddp_forward(name):
        return node
    elif operation_type == 'backward' and is_ddp_backward(name):
        return node
    elif operation_type == 'loss' and is_loss(name):
        return node
    elif operation_type == 'optimizer' and is_optimizer(name):
        return node
        
    # Search in children
    if 'children' in node:
        for child in node['children']:
            result = find_operation_in_tree(child, operation_type)
            if result:
                return result
                
    return None

def find_node_by_name(node: Dict[str, Any], target_name: str) -> Optional[Dict[str, Any]]:
    """Recursively find node by name in the tree"""
    if not node:
        return None
        
    if node.get('name') == target_name:
        return node
        
    for child in node.get('children', []):
        result = find_node_by_name(child, target_name)
        if result:
            return result
            
    return None

def process_tid_tree(root_node: Dict[str, Any]) -> Dict[int, Dict[str, Dict[str, Any]]]:
    """Process a single TID tree and organize its data by steps"""
    steps_data = defaultdict(dict)
    
    def process_step(step_node: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Process a single ProfilerStep node and look for operations"""
        step_data = {}
        operations = ['forward', 'backward', 'loss', 'optimizer']
        
        for child in step_node.get('children', []):
            for op_type in operations:
                if op_type not in step_data:  # Only look if we haven't found this operation yet
                    result = find_operation_in_tree(child, op_type)
                    if result:
                        step_data[op_type] = result
                        logger.debug(f"Found {op_type} operation")
        return step_data

    # Verify this is a CallTreeRoot
    if root_node.get('name') != 'CallTreeRoot':
        logger.debug("Not a CallTreeRoot node, skipping")
        return {}
        
    # Look through children of CallTreeRoot for ProfilerSteps
    for child in root_node.get('children', []):
        if child.get('type') == 'ProfilerStep':
            step_number = extract_step_number(child.get('name', ''))
            if step_number is not None:
                logger.debug(f"Processing ProfilerStep #{step_number}")
                step_data = process_step(child)
                if step_data:
                    steps_data[step_number] = step_data
                    logger.debug(f"Added data for step {step_number}")
    
    return dict(steps_data)

def parse_operator_trees(operator_trees: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Dict[int, Dict[str, Dict[str, Any]]]]]:
    """Parse operator trees and organize them by run, worker, step, and operation type"""
    parsed_data = {}
    
    for run_name, run_data in operator_trees.items():
        parsed_data[run_name] = {}
        
        for worker_name, worker_data in run_data.items():
            parsed_data[run_name][worker_name] = {}
            
            # Process each root node (TID) in the worker data
            for tid, root_node in worker_data.items():
                logger.debug(f"Processing TID {tid}")
                
                # Process this CallTreeRoot
                steps = process_tid_tree(root_node)
                
                if steps:
                    logger.debug(f"Found steps in TID {tid}")
                    # Merge steps into the final structure
                    for step_num, step_data in steps.items():
                        if step_num not in parsed_data[run_name][worker_name]:
                            parsed_data[run_name][worker_name][step_num] = {}
                            
                        # Update step data with new information
                        logger.debug(f"Adding data from TID {tid} to step {step_num}")
                        parsed_data[run_name][worker_name][step_num].update(step_data)
    
    return parsed_data
