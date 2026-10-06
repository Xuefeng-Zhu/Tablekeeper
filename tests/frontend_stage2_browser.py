"""Owner browser evidence. Run in df-harness-runner on an internal network.
BASE_URL, OLD_URL, EVIDENCE_DIR and CANDIDATE select the exact packaged services.
No secret requests, auth values or exports are written to evidence.
"""
import asyncio, json, os, pathlib, datetime
from playwright.async_api import async_playwright, expect
BASE=os.getenv('BASE_URL','http://frontend-s2-app:8080')
OLD=os.getenv('OLD_URL','http://frontend-s2-old:8080')
OUT=pathlib.Path(os.getenv('EVIDENCE_DIR','/out'))
OUT.mkdir(parents=True,exist_ok=True)
DATE='2030-06-06'
START=DATE+'T19:00'
F={'users':[{'id':'u','email':'owner@example.com','password':'correct horse','display_name':'Ada'},{'id':'v','email':'other@example.com','password':'correct horse','display_name':'Bob'}], 'restaurants':[], 'reservations':[]}
for rid,name in [('a','The Orchard'),('b','Evening House')]:
 F['restaurants'].append({'id':rid,'name':name,'timezone':'Europe/Berlin','slot_minutes':30,'reservation_duration_minutes':90,'cancellation_cutoff_minutes':120,'opening_hours':[{'weekday':d,'opens':'18:00','closes':'23:00'} for d in ['mon','tue','wed','thu','fri','sat','sun']], 'tables':[{'id':'A','label':'Window','capacity':2},{'id':'B','label':'Garden','capacity':4}], 'combinable':[['B','A']]})
results=[]
errors=[]
def tid(page,id):return page.get_by_test_id(id)
async def api(ctx,path,body=None,token=None,key=None,base=BASE):
 headers={}
 if token:headers['Authorization']='Bearer '+token
 if key:headers['Idempotency-Key']=key
 response=await ctx.request.fetch(base+path,method='POST' if body is not None else 'GET',data=body,headers=headers)
 return response,await response.json() if response.status!=204 else None
async def reset(ctx):
 r,_=await api(ctx,'/_test/reset',F);assert r.status==204
async def login(page):
 await page.goto(BASE+'/login');await tid(page,'login-email').fill('owner@example.com');await tid(page,'login-password').fill('correct horse');await tid(page,'login-submit').click();await expect(tid(page,'current-user')).to_contain_text('Ada')
async def search(page,rid='a',party=2,date=DATE):
 await tid(page,'restaurant-select').select_option(rid);await tid(page,'date-input').fill(date);await tid(page,'party-size-input').fill(str(party));await tid(page,'search-button').click();await expect(tid(page,'availability-grid')).to_be_visible()
async def setup(browser):
 ctx=await browser.new_context(viewport={'width':1280,'height':900},timezone_id='America/Los_Angeles');page=await ctx.new_page();page.on('pageerror',lambda error:errors.append(str(error)));await reset(ctx);await login(page);await search(page);return ctx,page
async def capture(page,name):
 for width,height in [(1280,900),(375,812)]:
  await page.set_viewport_size({'width':width,'height':height});await page.screenshot(path=str(OUT/f'{name}-{width}.png'),full_page=True)
  assert await page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth'),name
 await page.set_viewport_size({'width':1280,'height':900})
async def run_case(name,fn,browser):
 try:await fn(browser);results.append({'case':name,'result':'PASS'});print(name+' PASS',flush=True)
 except Exception as e:results.append({'case':name,'result':'FAIL','error':str(e)});print(name+' FAIL '+str(e),flush=True);raise
async def routes(browser):
 ctx=await browser.new_context();page=await ctx.new_page();await reset(ctx)
 for route in ['/','/signup','/login','/lookup']:
  response=await page.goto(BASE+route);assert response.status==200 and 'text/html' in response.headers['content-type'];await page.wait_for_load_state('networkidle');await capture(page,'route-'+(route.strip('/') or 'home'))
 await page.goto(BASE+'/signup');await tid(page,'signup-email').fill('fresh@example.com');await tid(page,'signup-password').fill('correct horse');await tid(page,'signup-display-name').fill('Fresh');await tid(page,'signup-submit').click();await expect(tid(page,'current-user')).to_contain_text('Fresh')
 for route in ['/signup','/login','/lookup','/']:
  await page.goto(BASE+route);await expect(tid(page,'current-user')).to_contain_text('Fresh')
 await tid(page,'logout-button').click();await expect(tid(page,'current-user')).to_have_count(0)
 await page.goto(BASE+'/login');await tid(page,'login-email').fill('owner@example.com');await tid(page,'login-password').fill('bad');await tid(page,'login-submit').click();await expect(tid(page,'auth-error')).to_be_visible();await capture(page,'auth-error');await ctx.close()
async def booking_and_lookup(browser):
 ctx,page=await setup(browser);await capture(page,'grid');await tid(page,'slot-B+A-19:00').focus();await page.keyboard.press('Enter');await expect(tid(page,'booking-summary')).to_contain_text('Garden & Window');await capture(page,'selected-pair')
 await tid(page,'booking-submit').click();await expect(tid(page,'confirmation')).to_be_visible();ref=await tid(page,'confirmation-reference').inner_text();await capture(page,'success-pair');await tid(page,'booking-submit').click();await expect(tid(page,'confirmation-reference')).to_have_text(ref)
 await page.goto(BASE+'/lookup');await tid(page,'lookup-reference-input').fill(ref);await tid(page,'lookup-submit').click();await expect(tid(page,'reservation-status')).to_have_text('confirmed');await expect(tid(page,'reservation-tables')).to_contain_text('Garden & Window');await capture(page,'lookup-confirmed');await tid(page,'reservation-cancel-button').click();await expect(tid(page,'reservation-status')).to_have_text('cancelled');await expect(tid(page,'reservation-cancel-button')).to_have_count(0);await capture(page,'lookup-cancelled')
 await tid(page,'lookup-reference-input').fill('MISSING');await tid(page,'lookup-submit').click();await expect(tid(page,'reservation-error')).to_be_visible();await expect(tid(page,'reservation-detail')).to_have_count(0);await ctx.close()
async def lost_and_replay(browser):
 for pair in [False,True]:
  ctx,page=await setup(browser);await tid(page,'slot-'+('B+A' if pair else 'A')+'-19:00').click();saved=[];responses=[]
  async def lose(route):
   req=route.request;saved.append((req.method,req.url,req.post_data,req.headers['idempotency-key'],req.headers['authorization']))
   response=await route.fetch();assert response.status==201;responses.append(await response.json());await route.abort('failed')
  await page.route('**/reservations',lose);await tid(page,'booking-submit').click();await expect(tid(page,'booking-uncertain')).to_be_visible();await expect(tid(page,'booking-error')).to_have_count(0);await expect(tid(page,'confirmation')).to_have_count(0);await capture(page,'uncertain-'+str(pair));await page.unroute('**/reservations',lose)
  async def record(route):
   req=route.request;saved.append((req.method,req.url,req.post_data,req.headers['idempotency-key'],req.headers['authorization']));response=await route.fetch();assert response.status==200;await route.fulfill(response=response)
  await page.route('**/reservations',record);await tid(page,'booking-submit').click();await expect(tid(page,'confirmation-reference')).to_have_text(responses[0]['reference']);assert saved[0]==saved[1];await expect(tid(page,'booking-uncertain')).to_have_count(0)
  await page.unroute('**/reservations',record)
  oldkey=saved[0][3];await tid(page,'booking-party-size').fill('1');await tid(page,'booking-party-size').fill('2')
  async def edited(route):
   assert route.request.headers['idempotency-key']!=oldkey;await route.continue_()
  await page.route('**/reservations',edited);await tid(page,'booking-submit').click();await expect(tid(page,'booking-error')).to_be_visible();await expect(tid(page,'confirmation')).to_have_count(0);await ctx.close()
async def conflict(browser):
 for pair in [False,True]:
  ctx,page=await setup(browser);await tid(page,'slot-'+('B+A' if pair else 'A')+'-19:00').click();await tid(page,'booking-party-size').fill('1')
  _,other=await api(ctx,'/auth/login',{'email':'other@example.com','password':'correct horse'})
  r,_=await api(ctx,'/reservations',{'restaurant_id':'a','table_id':'A','starts_at_local':START,'party_size':1},other['token'],'competitor');assert r.status==201
  await tid(page,'booking-submit').click();await expect(tid(page,'booking-error')).to_be_visible();await expect(tid(page,'booking-party-size')).to_have_value('1');await expect(tid(page,'slot-A-19:00')).to_have_attribute('data-available','false');await expect(tid(page,'confirmation')).to_have_count(0);await capture(page,'conflict-'+str(pair));await ctx.close()
async def search_races(browser):
 for endpoint in ['availability','restaurants/a']:
  for fail in [False,True]:
   ctx,page=await setup(browser);held=asyncio.Event();release=asyncio.Event()
   async def delay(route):
    if ('restaurant_id=a' in route.request.url if endpoint=='availability' else '/restaurants/a' in route.request.url):
     response=await route.fetch();held.set();await release.wait()
     if fail:await route.abort('failed')
     else:await route.fulfill(response=response)
    else:await route.continue_()
   await page.route('**/'+endpoint+'*',delay)
   await tid(page,'search-button').click();await held.wait();await search(page,'b',4);await tid(page,'slot-B-19:00').click();await tid(page,'booking-party-size').focus();release.set();await page.wait_for_timeout(150)
   await expect(tid(page,'booking-summary')).to_contain_text('Evening House');await expect(tid(page,'booking-party-size')).to_have_value('4');assert await tid(page,'booking-party-size').evaluate('(e)=>document.activeElement===e');await expect(tid(page,'search-error')).to_have_count(0);await ctx.close()
async def stale_post(browser):
 for fail in [False,True]:
  ctx,page=await setup(browser);await tid(page,'slot-A-19:00').click();held=asyncio.Event();release=asyncio.Event()
  async def delay(route):
   response=await route.fetch();assert response.status==201;held.set();await release.wait()
   if fail:await route.abort('failed')
   else:await route.fulfill(response=response)
  await page.route('**/reservations',delay);await tid(page,'booking-submit').click();await held.wait();await search(page,'b',4);await tid(page,'slot-B-19:00').click();await tid(page,'booking-party-size').focus();release.set();await page.wait_for_timeout(150)
  await expect(tid(page,'booking-summary')).to_contain_text('Evening House');await expect(tid(page,'confirmation')).to_have_count(0);await expect(tid(page,'booking-uncertain')).to_have_count(0);assert await tid(page,'booking-party-size').evaluate('(e)=>document.activeElement===e');await ctx.close()
async def upgrade(browser):
 ctx=await browser.new_context();page=await ctx.new_page();oldfixture=json.loads(json.dumps(F));[r.pop('combinable') for r in oldfixture['restaurants']];r,_=await api(ctx,'/_test/reset',oldfixture,base=OLD);assert r.status==204
 switched=False;lost=True;saved=[];original=None
 async def old_api(route):
  nonlocal lost,original
  req=route.request;path=req.url[len(BASE):]
  if path.startswith('/static/') or path.split('?')[0] in ['/','/login','/signup','/lookup']:await route.continue_();return
  if path=='/reservations' and req.method=='POST':saved.append((req.post_data,req.headers['idempotency-key'],req.headers['authorization']))
  response=await route.fetch(url=(BASE if switched else OLD)+path)
  if path=='/reservations' and req.method=='POST' and lost:
   assert response.status==201;original=await response.json();assert 'table_ids' not in original;lost=False;await route.abort('failed')
  else:
   if path=='/reservations' and req.method=='POST':assert response.status==200 and await response.json()==original
   await route.fulfill(response=response)
 await page.route('**/*',old_api);await login(page);await search(page);await tid(page,'slot-A-19:00').click();await tid(page,'booking-submit').click();await expect(tid(page,'booking-uncertain')).to_be_visible()
 # Actual Stage1 commit/export, cancellation, then Stage2 import between requests.
 token=saved[0][2].split(' ',1)[1];r,_=await api(ctx,'/reservations/'+original['reference']+'/cancel',{},token,base=OLD);assert r.status==200
 _,snapshot=await api(ctx,'/_test/export',base=OLD);await reset(ctx);r,_=await api(ctx,'/_test/import',snapshot);assert r.status==204;switched=True
 await expect(tid(page,'current-user')).to_contain_text('Ada');await expect(tid(page,'booking-party-size')).to_have_value('2');await tid(page,'booking-submit').click();await expect(tid(page,'confirmation-reference')).to_have_text(original['reference']);assert saved[0]==saved[1];await expect(tid(page,'confirmation-tables')).to_contain_text('Window');await capture(page,'upgrade-original-receipt')
 await page.goto(BASE+'/lookup');await tid(page,'lookup-reference-input').fill(original['reference']);await tid(page,'lookup-submit').click();await expect(tid(page,'reservation-status')).to_have_text('cancelled');await expect(tid(page,'reservation-cancel-button')).to_have_count(0);await ctx.close()
async def empty_and_bounds(browser):
 ctx,page=await setup(browser)
 await search(page,party=1000000000000);await expect(tid(page,'slot-A-19:00')).to_have_attribute('data-available','false');await capture(page,'full-grid')
 for date in ['0001-06-06','9999-06-06']:
  await search(page,date=date);await expect(tid(page,'date-input')).to_have_value(date);await tid(page,'slot-A-19:00').click();await expect(tid(page,'booking-summary')).to_contain_text(date)
 closed=json.loads(json.dumps(F));closed['restaurants'][0]['opening_hours']=[];await api(ctx,'/_test/reset',closed);await page.goto(BASE+'/');await tid(page,'restaurant-select').select_option('a');await tid(page,'search-button').click();await expect(tid(page,'no-slots')).to_be_visible();await expect(tid(page,'availability-grid')).to_have_count(0);await capture(page,'empty');await ctx.close()
async def main():
 started=datetime.datetime.now(datetime.timezone.utc).isoformat()
 try:
  async with async_playwright() as p:
   browser=await p.chromium.launch(headless=True);version=browser.version
   for name,fn in [('routes-auth',routes),('pair-replay-lookup-cancel-keyboard',booking_and_lookup),('single-pair-lost-commit-exact-retry-edit-revert',lost_and_replay),('single-pair-conflict-preservation',conflict),('latest-detail-availability-success-error',search_races),('stale-post-success-uncertain',stale_post),('actual-stage1-browser-import-retry-original-vs-current',upgrade),('full-empty-calendar-bounds',empty_and_bounds)]:await run_case(name,fn,browser)
   assert not errors,errors;await browser.close()
 finally:
  report={'work_item':'UI-S2','candidate':os.getenv('CANDIDATE'),'responsible':'@frankzhu94/factory-frontend','layer':'actual Chromium + actual packaged API, internal Docker network; route holds/aborts forward real response bytes','started_utc':started,'ended_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'browser':locals().get('version'),'results':results,'page_errors':errors,'cost':'UNAVAILABLE','limitations':['Finite owner cases; independent QA/Designer/Reviewer acceptance pending. No exhaustive schedule or continuous resource profile.']}
  (OUT/'report.json').write_text(json.dumps(report,indent=2))
asyncio.run(main())
