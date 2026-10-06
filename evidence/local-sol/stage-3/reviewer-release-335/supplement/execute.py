import pathlib,subprocess,json,datetime
p=pathlib.Path('/Users/frank/mygit/Tablekeeper/result-local-sol-20261005/.evidence/reviewer-s3-release-20261006T0813Z/supplement');records=[]
def run(a):
 st=datetime.datetime.now(datetime.timezone.utc).isoformat();r=subprocess.run(a,capture_output=True,text=True);records.append({'argv':a,'start_utc':st,'end_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr});(p/'execution.json').write_text(json.dumps(records,indent=2)+'\n');print(r.returncode,a[:3],flush=True);assert r.returncode==0
run(['docker','network','create','--internal','review-s3-net-0813c'])
for name,image in [('source','tablekeeper-review-s3:0813'),('dest','tablekeeper-review-s3:0813'),('old1','tablekeeper-review-s3-old1:0813'),('old2','tablekeeper-review-s3-old2:0813')]:run(['docker','run','-d','--name','review-s3-'+name+'-0813c','--network','review-s3-net-0813c','--cpus','2','--memory','2g',image])
run(['docker','run','--rm','--network','review-s3-net-0813c','--cpus','2','--memory','2g','-v',str(p)+':/evidence','--entrypoint','python','df-harness-runner','/evidence/review.py','prepare'])
run(['docker','stop','--time','2','review-s3-source-0813c'])
run(['docker','run','--rm','--network','review-s3-net-0813c','--cpus','2','--memory','2g','-v',str(p)+':/evidence','--entrypoint','python','df-harness-runner','/evidence/review.py','verify'])
run(['docker','rm','-f',*["review-s3-"+n+"-0813c" for n in ['source','dest','old1','old2']]])
run(['docker','network','rm','review-s3-net-0813c'])
