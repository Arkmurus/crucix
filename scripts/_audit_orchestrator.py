"""Analyze dd_orchestrator error handling patterns."""
with open('aria_service/intel/dd_orchestrator.py', encoding='utf-8') as f:
    content = f.read()

import re
try_blocks = len(re.findall(r'try:', content))
except_blocks = len(re.findall(r'except', content))
bare_excepts = len(re.findall(r'except\s*:', content))
ws_calls = content.count('wire_success')
wf_calls = content.count('wire_failure')
fw_decos = content.count('fail_wire')
bh_refs = content.count('brain_hook')
absorb_calls = content.count('.absorb(')
record_signal = content.count('record_signal')
data_gaps = content.count('data_gaps.append')
findings_append = content.count('findings.append')

print(f'DD Orchestrator Error Handling Profile:')
print(f'  try blocks:          {try_blocks}')
print(f'  except blocks:       {except_blocks}')
print(f'  bare excepts:        {bare_excepts}')
print(f'  wire_success calls:  {ws_calls}')
print(f'  wire_failure calls:  {wf_calls}')
print(f'  fail_wire decorators:{fw_decos}')
print(f'  brain_hook refs:     {bh_refs}')
print(f'  absorb calls:        {absorb_calls}')
print(f'  record_signal calls: {record_signal}')
print(f'  data_gaps.append:    {data_gaps}')
print(f'  findings.append:     {findings_append}')
