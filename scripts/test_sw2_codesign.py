#!/usr/bin/env python3
"""Focused design-time contract tests for Sub-Workflow 2."""
import contextlib, importlib.util, io, json, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def load(path):
 spec=importlib.util.spec_from_file_location(path.stem,path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
cases=load(ROOT/'skills/my-designer/scripts/verify_design_cases.py')
class DesignCasesTests(unittest.TestCase):
 def test_all_platforms_route(self):
  for platform in ('desktop','mobile','web'):
   out=io.StringIO()
   with contextlib.redirect_stdout(out): self.assertEqual(cases.main(['route','--platform',platform,'--dimensions','visual-style']),0)
   payload=json.loads(out.getvalue()); self.assertEqual(payload['platform'],platform)
 def test_register_auto_case(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'cases.json'; p.write_text(json.dumps({'active_cases':[]}),encoding='utf-8')
   self.assertEqual(cases.main(['register-case','--cases-file',str(p),'--id','auto','--title','Generic case','--platforms','desktop,mobile,web','--symptom','x','--fix-rule','y']),0)
   self.assertEqual(json.loads(p.read_text())['active_cases'][0]['id'],'DC-001')
 def test_removed_implementation_audits(self):
  self.assertNotIn('audit-code', cases.build_parser().format_help())
  self.assertNotIn('audit-l10n', cases.build_parser().format_help())
if __name__=='__main__': unittest.main()
