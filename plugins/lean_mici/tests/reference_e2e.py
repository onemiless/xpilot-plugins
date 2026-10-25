#!/usr/bin/env python3
"""Compare captured real-phone outputs to the pinned upstream decoder, independently of stock weights."""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from plugins.lean_mici.profile import decode

p=argparse.ArgumentParser()
p.add_argument('--parser',type=Path,required=True)
p.add_argument('--layout',type=Path,required=True)
p.add_argument('--outputs',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
spec=importlib.util.spec_from_file_location('reference_parser',a.parser)
reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
node=next(n for n in ast.parse(a.layout.read_text()).body if isinstance(n,ast.AnnAssign) and getattr(n.target,'id',None)=='BIG_OUTPUT_SLICES')
slices=eval(compile(ast.Expression(node.value),str(a.layout),'eval'),{'slice':slice})
rows=np.load(a.outputs)['outputs'];count=0
for raw in rows:
 expected=reference.Parser().parse_outputs({k:raw[s][None].copy() for k,s in slices.items()})
 actual=decode(raw.copy())
 for key,value in expected.items():
  np.testing.assert_array_equal(actual[key],value,err_msg=key)
  count+=value.size
result={'pass':True,'frames':len(rows),'values_compared':count,'exact_decoder_match':True,
        'stock_weight_equivalence':None,'sha256':{str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in [a.parser,a.layout,a.outputs]}}
(a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
(a.output/'frames.csv').write_text('frameId,decoder_match\n'+''.join(f'{i},1\n' for i in range(len(rows))))
(a.output/'wire.log').write_text('Inputs are the hash-pinned outputs of retained real-phone BGM1 v4 wire.\n')
(a.output/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(a.output.iterdir()) if p.name!='SHA256SUMS'))
print(json.dumps(result,indent=2))
