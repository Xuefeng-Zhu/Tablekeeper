import subprocess,datetime,json,pathlib
p=pathlib.Path('/Users/frank/mygit/Tablekeeper/result-local-sol-20261005/.evidence/reviewer-s3-release-20261006T0813Z');records=[]
def run(args,log=None):
 start=datetime.datetime.now(datetime.timezone.utc).isoformat();r=subprocess.run(args,capture_output=True,text=True);end=datetime.datetime.now(datetime.timezone.utc).isoformat();records.append({'argv':args,'start_utc':start,'end_utc':end,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr});p.joinpath('execution.json').write_text(json.dumps(records,indent=2)+'\n')
 if log:p.joinpath(log).write_text(r.stdout+r.stderr)
 print('exit',r.returncode,args[0:3],flush=True)
 if r.returncode:raise RuntimeError('Command failed; evidence retained')
run(['docker','build','-t','tablekeeper-review-s3:0813','/private/tmp/tablekeeper-review-s3-release-20261006T0813Z/stage-3'],'build.txt')
run(['docker','build','-t','tablekeeper-review-s3-old1:0813','/private/tmp/tablekeeper-review-s3-release-20261006T0813Z/stage-1'],'build-old.txt')
run(['docker','build','-t','tablekeeper-review-s3-old2:0813','/private/tmp/tablekeeper-review-s3-release-20261006T0813Z/stage-2'],'build-old2.txt')
run(['docker','network','create','--internal','review-s3-net-0813'])
run(['docker','run','-d','--name','review-s3-app-0813','--network','review-s3-net-0813','--cpus','2','--memory','2g','tablekeeper-review-s3:0813'])
run(['docker','run','-d','--name','review-s3-old1-0813','--network','review-s3-net-0813','--cpus','2','--memory','2g','tablekeeper-review-s3-old1:0813'])
run(['docker','run','-d','--name','review-s3-old2-0813','--network','review-s3-net-0813','--cpus','2','--memory','2g','tablekeeper-review-s3-old2:0813'])
run(['docker','run','-d','--name','review-s3-dest-0813','--network','review-s3-net-0813','--cpus','2','--memory','2g','tablekeeper-review-s3:0813'])
run(['docker','create','--name','review-s3-browser-0813','--network','review-s3-net-0813','--cpus','2','--memory','2g','--entrypoint','python','df-harness-runner','/review.py'])
run(['docker','cp',str(p/'review.py'),'review-s3-browser-0813:/review.py'])
try:run(['docker','start','-a','review-s3-browser-0813'],'browser.txt')
except RuntimeError:pass
run(['docker','cp','review-s3-browser-0813:/out',str(p/'browser-run')])
run(['docker','inspect','review-s3-app-0813','review-s3-browser-0813','--format','{{.Name}} image={{.Image}} cpu={{.HostConfig.NanoCpus}} memory={{.HostConfig.Memory}} start={{.State.StartedAt}} end={{.State.FinishedAt}} exit={{.State.ExitCode}}'])
run(['docker','network','inspect','review-s3-net-0813','--format','internal={{.Internal}}'])
run(['docker','rm','-f','review-s3-browser-0813','review-s3-app-0813','review-s3-old1-0813','review-s3-old2-0813','review-s3-dest-0813'])
run(['docker','network','rm','review-s3-net-0813'])
