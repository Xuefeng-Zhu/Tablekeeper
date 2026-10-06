import datetime,json,sys,time,unittest
from pathlib import Path
sys.path.insert(0,'/work/tests')
import backend_stage3_http as stage3
import backend_stage3_inherited as inherited
import backend_stage2_pairs as pairs
import backend_stage2_inherited as helper
start=datetime.datetime.now(datetime.timezone.utc).isoformat();durations=[];status_counts={}
def install(cls):
    original=cls.request
    def timed(self,*args,**kwargs):
        before=time.monotonic();result=original(self,*args,**kwargs);durations.append(time.monotonic()-before)
        status_counts[str(result[0])]=status_counts.get(str(result[0]),0)+1
        return result
    cls.request=timed
install(helper.HTTPTests);install(inherited.HTTPTests)
suite=unittest.TestSuite()
for cls in [stage3.Stage3Tests,inherited.HTTPTests,pairs.PairTests]:suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
result=unittest.TextTestRunner(verbosity=2).run(suite)
record={'work_item':'BUILD-S3','candidate':'335f1677316e4a9dfe0cff43ec41ff0d9b0b25a3','responsible':'@frankzhu94/factory-backend','start_utc':start,'end_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'requests':len(durations),'status_counts':status_counts,'max_request_seconds':max(durations),'layer':'four actual packaged offline internal-network services Stage3x2/acceptedStage1/acceptedStage2;2CPU/2GiB each; Python3.12 runner','result':'PASS' if result.wasSuccessful() else 'FAIL'}
Path('/evidence/candidate-record.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record));sys.exit(not result.wasSuccessful())
