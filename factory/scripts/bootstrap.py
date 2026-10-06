#!/usr/bin/env python3
"""Recreate local dependencies and a pinned read-only challenge; never dispatch."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,sys

def run(args,cwd=None,env=None):
 print('+',__import__('shlex').join(map(str,args)),flush=True)
 subprocess.run(list(map(str,args)),cwd=cwd,env=env,check=True)

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--install-browser',action='store_true');a=p.parse_args()
 factory=Path(__file__).resolve().parents[1]; workspace=factory.parent; challenge=workspace/'challenge';runs=workspace/'runs'
 lock=json.loads((factory/'config/source-lock.json').read_text())
 uv=shutil.which('uv');git=shutil.which('git');npm=shutil.which('npm')
 if not uv or not git or not npm: raise SystemExit('Install uv, Git, and npm first; no global installation is performed here.')
 if not challenge.exists():
  run([git,'clone','--no-checkout',lock['challenge']['url'],challenge]);run([git,'-C',challenge,'checkout','--detach',lock['challenge']['commit']])
 actual=subprocess.check_output([git,'-C',str(challenge),'rev-parse','HEAD'],text=True).strip()
 if actual!=lock['challenge']['commit']:raise SystemExit('Existing challenge commit differs. Refusing refresh or overwrite.')
 for name,expected in lock['challenge']['files'].items():
  file=challenge/name
  if hashlib.sha256(file.read_bytes()).hexdigest()!=expected:raise SystemExit('Pinned source mismatch: '+name)
 runs.mkdir(exist_ok=True)
 env=dict(os.environ,UV_CACHE_DIR=str(runs/'uv-cache'),PLAYWRIGHT_BROWSERS_PATH=str(runs/'browsers'),npm_config_cache=str(runs/'npm-cache'))
 run([npm,'ci','--ignore-scripts','--no-audit','--no-fund','--prefix',factory/'tooling/codex'],env=env)
 run([uv,'sync','--project',factory,'--frozen','--python','3.13.5'],env=env)
 harness=runs/'harness-venv'
 if not harness.exists():run([uv,'venv',harness,'--python','3.13.5'],env=env)
 run([uv,'pip','sync','--python',harness/'bin/python','--require-hashes',factory/'config/harness-requirements.lock'],env=env)
 if a.install_browser:run([harness/'bin/python','-m','playwright','install','chromium'],env=env)
 # Read-only source mount is enforced separately by the chosen agent environment.
 for file in challenge.rglob('*'):
  if file.is_file():file.chmod(file.stat().st_mode & ~0o222)
 for directory in sorted([x for x in challenge.rglob('*') if x.is_dir()],key=lambda p:len(p.parts),reverse=True):directory.chmod(directory.stat().st_mode & ~0o222)
 challenge.chmod(challenge.stat().st_mode & ~0o222)
 print('Dependencies restored from locks. No credentials, seats, judged code, or dispatch created.')
 print('If relocated: update every absolute path in config/factory.yaml and scripts/codex-local; regenerate tasks and freeze. Old readiness evidence is invalid.')
if __name__=='__main__':main()
