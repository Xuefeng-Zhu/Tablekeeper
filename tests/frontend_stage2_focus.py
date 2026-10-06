"""R-F1 owner regression: actual keyboard and real HTTP on internal Docker network.
Run with the official df-harness-runner, BASE_URL/CANDIDATE/EVIDENCE_DIR.
No bearer values, passwords, private snapshots or stored retry keys are logged.
"""
import asyncio, json, os, datetime
from playwright.async_api import async_playwright, expect
from frontend_stage2_browser import setup, tid, api, search, BASE, OUT, START
ROWS=[]
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
async def focused(page):
 return await page.evaluate("({tag:document.activeElement.tagName,testid:document.activeElement.getAttribute('data-testid'),y:scrollY})")
async def check(browser,width,pair,state,control=None):
 ctx,page=await setup(browser)
 await page.set_viewport_size({'width':width,'height':900 if width==1280 else 812})
 await tid(page,'slot-'+('B+A' if pair else 'A')+'-19:00').focus()
 await page.keyboard.press('Enter');await page.keyboard.press('Tab')
 assert await tid(page,'booking-party-size').evaluate('(e)=>e===document.activeElement')
 await tid(page,'booking-party-size').fill('1');await page.keyboard.press('Tab')
 assert await tid(page,'booking-submit').evaluate('(e)=>e===document.activeElement')
 if state=='conflict':
  _,other=await api(ctx,'/auth/login',{'email':'other@example.com','password':'correct horse'})
  r,_=await api(ctx,'/reservations',{'restaurant_id':'a','table_id':'A','starts_at_local':START,'party_size':1},other['token'],'focus-competitor');assert r.status==201
 held=asyncio.Event();release=asyncio.Event();settled=asyncio.Event();requests=[];statuses=[];responses=[]
 async def hold(route):
  req=route.request;requests.append((req.method,req.url,req.post_data,req.headers['idempotency-key'],req.headers['authorization']))
  response=await route.fetch();statuses.append(response.status);assert response.status==(409 if state=='conflict' else 201)
  responses.append(await response.json());held.set();await release.wait()
  if state=='uncertain':await route.abort('failed')
  else:await route.fulfill(response=response)
  settled.set()
 await page.route('**/reservations',hold);before=await focused(page);await page.keyboard.press('Enter');await asyncio.wait_for(held.wait(),10)
 await expect(tid(page,'booking-submit')).to_have_attribute('aria-disabled','true')
 await expect(tid(page,'booking-form')).to_have_attribute('aria-busy','true')
 await expect(tid(page,'booking-submit')).to_have_text('Confirming your reservation…')
 pending=await focused(page);assert pending['testid']=='booking-submit',pending
 bounds=await tid(page,'booking-submit').evaluate('(e)=>({top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom,height:innerHeight})');assert bounds['top']>=7 and bounds['bottom']<=bounds['height']-7,bounds
 pending_style=await tid(page,'booking-submit').evaluate('(e)=>({border:getComputedStyle(e).borderStyle,outline:getComputedStyle(e).outlineStyle,outlineWidth:getComputedStyle(e).outlineWidth})')
 assert pending_style['border']=='dashed' and pending_style['outline']=='solid' and pending_style['outlineWidth']=='3px',pending_style
 # Real keyboard repeat activation, both Enter and Space, must not send another POST.
 await page.keyboard.press('Enter');await page.keyboard.press('Space');await page.wait_for_timeout(100);assert len(requests)==1, len(requests)
 name=f'{width}-{pair}-{state}-{control or "current"}'
 await page.screenshot(path=str(OUT/(name+'-pending.png')),full_page=False)
 if control=='user-moved':
  await tid(page,'party-size-input').focus()
 elif control=='keyboard-moved':
  await page.keyboard.press('Shift+Tab') # Disabled party is skipped; leave the submit naturally.
  assert not await tid(page,'booking-submit').evaluate('(e)=>e===document.activeElement')
 elif control in ['new-form','new-search']:
  await search(page,'b',4)
  if control=='new-form':await tid(page,'slot-B-19:00').click();await tid(page,'booking-party-size').focus()
  else:await tid(page,'party-size-input').focus()
 elif control=='logout':
  await tid(page,'logout-button').click();await tid(page,'party-size-input').focus()
 control_before=await focused(page);release.set();await asyncio.wait_for(settled.wait(),10)
 if control in ['new-form','new-search','logout']:
  await page.wait_for_timeout(150)
  await expect(tid(page,'confirmation')).to_have_count(0);await expect(tid(page,'booking-error')).to_have_count(0);await expect(tid(page,'booking-uncertain')).to_have_count(0)
  if control=='new-form':await expect(tid(page,'booking-summary')).to_contain_text('Evening House');await expect(tid(page,'booking-party-size')).to_have_value('4')
  else:await expect(tid(page,'booking-form')).to_have_count(0)
 else:
  await expect(tid(page,'confirmation' if state=='success' else 'booking-'+('error' if state=='conflict' else 'uncertain'))).to_be_visible()
  await expect(tid(page,'booking-submit')).not_to_have_attribute('aria-disabled','true')
  await expect(tid(page,'booking-form')).to_have_attribute('aria-busy','false')
  await expect(tid(page,'booking-party-size')).to_have_value('1')
  if state=='conflict':await expect(tid(page,'slot-A-19:00')).to_have_attribute('data-available','false')
  await page.wait_for_timeout(100)
 after=await focused(page)
 assert after['testid']==(control_before['testid'] if control else 'booking-submit'),(name,control_before,after)
 if not control:
  bounds=await tid(page,'booking-submit').evaluate('(e)=>({top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom,height:innerHeight})');assert bounds['top']>=7 and bounds['bottom']<=bounds['height']-7,bounds
 await page.screenshot(path=str(OUT/(name+'-after.png')),full_page=False)
 # A current uncertainty is recoverable through keyboard with the exact identity.
 retry_identity=None
 if state=='uncertain' and control is None:
  await page.unroute('**/reservations',hold)
  async def replay(route):
   req=route.request;assert requests[0]==(req.method,req.url,req.post_data,req.headers['idempotency-key'],req.headers['authorization']);response=await route.fetch();assert response.status==200 and await response.json()==responses[0];await route.fulfill(response=response)
  await page.route('**/reservations',replay);await page.keyboard.press('Space');await expect(tid(page,'confirmation-reference')).to_have_text(responses[0]['reference']);assert (await focused(page))['testid']=='booking-submit';retry_identity=True
 ROWS.append({'case':name,'viewport':[width,900 if width==1280 else 812],'pair':pair,'state':state,'control':control,'before':before,'pending':pending,'control_before':control_before,'after':after,'pending_style':pending_style,'real_server_statuses':statuses,'pending_post_count':len(requests),'enter_space_duplicates_suppressed':True,'exact_uncertain_retry':retry_identity,'result':'PASS','utc':utc()});print(name+' PASS',flush=True)
 await ctx.close()
async def main():
 start=utc();version=None
 try:
  async with async_playwright() as p:
   browser=await p.chromium.launch(headless=True);version=browser.version
   for width in [1280,375]:
    for pair in [False,True]:
     for state in ['success','conflict','uncertain']:await check(browser,width,pair,state)
   for control in ['user-moved','keyboard-moved','new-form','new-search','logout']:
    for state in ['success','conflict','uncertain']:await check(browser,375,True,state,control)
   await browser.close()
 finally:
  (OUT/'focus-report.json').write_text(json.dumps({'work_item':'UI-S2 repair1 R-F1','candidate':os.getenv('CANDIDATE'),'responsible':'@frankzhu94/factory-frontend','utc_start':start,'utc_end':utc(),'browser':version,'records':ROWS,'expected_cases':27,'result':'PASS' if len(ROWS)==27 else 'FAIL/incomplete','layer':'Actual Docker Chromium keyboard Enter/Space + real service409/201; hold/abort actual bytes; unchanged retry200','limitations':['Owner checks only; independent Reviewer and Designer rendered gates pending.','Native browser zoom/assistive audio and exhaustive schedules NOT_TESTED.'],'tokens_logged':False},indent=2))
if __name__=='__main__':asyncio.run(main())
