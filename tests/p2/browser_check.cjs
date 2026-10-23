/* Run with CGS_TEST_PYTHON, CGS_TEST_PLAYWRIGHT, CGS_TEST_BROWSER and CGS_TEST_OUTPUT. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {mkdir} = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require(process.env.CGS_TEST_PLAYWRIGHT || 'playwright');
const root = path.resolve(__dirname,'../..');
const output = process.env.CGS_TEST_OUTPUT;
if (!output || !process.env.CGS_TEST_PYTHON) throw Error('Set test-only runtime and output paths.');

async function server(unconfigured=false) {
  const processHandle=spawn(process.env.CGS_TEST_PYTHON,['-B',path.join(__dirname,'preview_server.py'),
    '--synthetic-test-data','--db-root',path.join(output,unconfigured?'unconfigured-db':'configured-db'),
    ...(unconfigured?['--unconfigured']:[])],{cwd:root,windowsHide:true,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
  let diagnostic='';
  processHandle.stderr.on('data',x=>diagnostic+=x.toString());
  const url=await new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>reject(Error('Test server timeout: '+diagnostic)),15000);
    let text='';
    processHandle.stdout.on('data',data=>{
      text+=data;
      if(text.includes('\n')) { clearTimeout(timeout); try { resolve(JSON.parse(text.split('\n')[0]).url); } catch(e) { reject(e); } }
    });
    processHandle.on('exit',code=>{ clearTimeout(timeout); reject(Error('Test server exited '+code+': '+diagnostic)); });
  });
  return {url,stop:()=>{const done=new Promise(resolve=>processHandle.once('exit',resolve));processHandle.kill();return done;}};
}

(async()=>{
  await mkdir(output,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CGS_TEST_BROWSER});
  let backend;
  try {
    backend=await server();
    const context=await browser.newContext({viewport:{width:1440,height:1080}});
    const page=await context.newPage();
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    // A browser test must fail before any accidental external request.
    await context.route('**/*',route=>{
      const url=new URL(route.request().url());
      return ['127.0.0.1','localhost'].includes(url.hostname) ? route.continue() : route.abort();
    });
    await page.goto(backend.url);
    await page.getByRole('button',{name:'Lead research',exact:true}).click();
    await page.locator('#r-status').getByRole('heading',{name:'Synthetic Factory 1'}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Start research',exact:true}).isEnabled(),true);
    await page.getByRole('button',{name:'Save an opportunity…',exact:true}).click();
    await page.getByLabel('Opportunity title',{exact:true}).fill('Synthetic planning opportunity <script>window.bad=true</script>');
    await page.getByLabel('Team / business unit (optional)').fill('Operations');
    // The server commits, but the response is lost. The same form retry must not duplicate the record.
    await page.route('**/api/research/opportunities', async route=>{
      if(route.request().method()!=='POST') return route.continue();
      await route.fetch();
      await route.abort();
      await page.unroute('**/api/research/opportunities');
    });
    await page.getByRole('button',{name:'Save opportunity',exact:true}).click();
    await page.locator('#r-dialog-error').waitFor({state:'visible'});
    await page.getByRole('button',{name:'Save opportunity',exact:true}).click();
    await page.locator('#r-opportunities').getByText('Awaiting review',{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>window.bad),undefined);
    await page.getByRole('button',{name:'Record human review…',exact:true}).click();
    await page.getByLabel('Reviewer / consultant').fill('Test consultant');
    await page.getByLabel('Decision',{exact:true}).selectOption('worth_exploring');
    await page.getByLabel('Reason',{exact:true}).selectOption('relevant_signal');
    if(process.env.CGS_TEST_P4==='1') await page.getByLabel('Feedback applies to').selectOption('theme');
    await page.getByRole('button',{name:'Save review',exact:true}).click();
    await page.locator('#r-dialog-error').getByText('Check the cited sources',{exact:false}).waitFor();
    await page.getByLabel('I checked the cited facts',{exact:false}).check();
    await page.getByRole('button',{name:'Save review',exact:true}).click();
    await page.locator('#r-opportunities .badge').getByText('Worth exploring',{exact:true}).waitFor();
    await page.reload();
    await page.getByRole('button',{name:'Lead research',exact:true}).click();
    await page.locator('#r-opportunities .badge').getByText('Worth exploring',{exact:true}).waitFor();
    await page.locator('#r-opportunities summary').filter({hasText:'Cited facts'}).click();
    assert.ok(await page.locator('#r-opportunities a').count()>0);
    await page.screenshot({path:path.join(output,'research-desktop.png'),fullPage:true});
    await page.getByLabel('Consultant',{exact:true}).fill('Test consultant');
    await page.getByLabel('Company name',{exact:true}).fill('Synthetic Factory 1');
    await page.getByLabel('Public website domain').fill('synthetic-factory-1.example');
    await page.getByRole('button',{name:'Start research',exact:true}).click();
    await page.getByRole('button',{name:'Stop research',exact:true}).click();
    await page.getByRole('button',{name:'Resume saved task',exact:true}).waitFor();
    await page.getByRole('button',{name:'Resume saved task',exact:true}).click();
    await page.locator('#r-status .badge').getByText('completed',{exact:true}).waitFor({timeout:20000});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.querySelector('#sidebar').getBoundingClientRect().right<=1);
    await page.screenshot({path:path.join(output,'research-mobile.png'),fullPage:true,animations:'disabled'});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.setViewportSize({width:1440,height:1080});
    await page.getByRole('button',{name:/Work queue/}).click();
    await page.getByRole('button',{name:'Add a case',exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    const stored=await page.evaluate(async()=> (await (await fetch('/api/research/opportunities')).json()).result);
    assert.equal(stored.length,1);
    assert.equal(stored[0].feedback_history.length,1);
    assert.equal(stored[0].latest_feedback.feedback.review_minutes,null);
    if(process.env.CGS_TEST_P3==='1') {
      await page.getByRole('button',{name:'Lead research',exact:true}).click();
      await page.getByRole('button',{name:'Submit for acceptance',exact:true}).click();
      await page.locator('#r-handoffs').getByRole('button',{name:'Review handoff…',exact:true}).click();
      await page.getByLabel('Receiving reviewer',{exact:true}).fill('Receiving reviewer');
      await page.getByLabel('Accountable owner',{exact:true}).fill('Assigned consultant');
      const due=new Date(Date.now()+3*86400000).toISOString().slice(0,10);
      await page.getByLabel('Next-action due date (system date)').fill(due);
      await page.getByLabel('I checked existing cases',{exact:false}).check();
      await page.getByLabel('Relationship / duplicate-check conclusion').fill('Reviewed local cases and known relationships; distinct synthetic opportunity.');
      await page.route('**/api/research/handoffs/*/decision',async route=>{
        await route.fetch(); await route.abort(); await page.unroute('**/api/research/handoffs/*/decision');
      });
      await page.getByRole('button',{name:'Save receiving decision',exact:true}).click();
      await page.locator('#r-dialog-error').waitFor({state:'visible'});
      await page.getByRole('button',{name:'Save receiving decision',exact:true}).click();
      await page.locator('#r-handoffs').getByRole('button',{name:'Open qualification case',exact:true}).click();
      await page.locator('.case-sub .badge').getByText('Not contacted',{exact:true}).waitFor();
      assert.equal(await page.getByRole('button',{name:'Generate outreach draft',exact:true}).isDisabled(),true);
      await page.getByRole('heading',{name:'Research handoff background',exact:true}).waitFor();
      await page.screenshot({path:path.join(output,'handoff-not-contacted.png'),fullPage:true,animations:'disabled'});
      await page.getByRole('button',{name:'Confirm contact…',exact:true}).click();
      await page.getByLabel('Contact name',{exact:true}).fill('Synthetic Person');
      await page.getByLabel('Role',{exact:true}).fill('Operations lead');
      await page.getByLabel('Source and verification rationale').fill('Consultant verified this synthetic contact for the test.');
      await page.getByRole('button',{name:'Save contact',exact:true}).click();
      await page.getByRole('button',{name:'Generate outreach draft',exact:true}).click();
      await page.getByLabel('Suggested subject').waitFor();
      await page.locator('.case-sub .badge').getByText('Not contacted',{exact:true}).waitFor();
      await page.getByRole('button',{name:'Record first outreach…',exact:true}).click();
      await page.getByLabel('What was sent and through which channel').fill('Human records sending an introduction outside this app.');
      await page.getByRole('button',{name:'Record outreach sent',exact:true}).click();
      await page.locator('.case-sub .badge').getByText('Contacted',{exact:true}).waitFor();
      await page.getByRole('button',{name:'Record reply & analyze',exact:true}).click();
      await page.getByLabel('Source label',{exact:true}).fill('Synthetic courtesy reply');
      await page.getByLabel('Original email or meeting note',{exact:true}).fill('Thank you for reaching out.');
      await page.getByRole('button',{name:'Save source & analyze',exact:true}).click();
      await page.locator('.case-sub .badge').getByText('Active',{exact:true}).waitFor();
      const data=await page.evaluate(async()=>await (await fetch('/api/bootstrap')).json());
      const received=data.cases.filter(x=>x.sourceOpportunityId===stored[0].opportunity.opportunity_id);
      assert.equal(received.length,1);
      assert.equal(received[0].contactStatus,'reply_received');
      assert.ok(Object.values(received[0].scores).every(x=>x===null));
      assert.equal(received[0].evidence.length,0);
      assert.equal(data.cases.length,8);
      assert.deepEqual(errors,[]);
      console.log('P3 browser check passed: submit/accept, lost-response retry, unknown contact, confirm contact, draft leaves stage unchanged, record outreach, reply becomes Active without confirmed scores.');
    }
    if(process.env.CGS_TEST_P4==='1') {
      if(await page.locator('#dialog').evaluate(d=>d.open)) await page.getByRole('button',{name:'Close dialog',exact:true}).click();
      await page.getByRole('button',{name:'Lead research',exact:true}).click();
      await page.getByRole('button',{name:'Create manual proposal…',exact:true}).click();
      await page.getByLabel('Direction patch (JSON)').fill(JSON.stringify({signals:['Leadership integration reviews']}));
      await page.getByLabel('Feedback-based rationale').fill('Selected theme feedback asks for more specific events.');
      await page.getByRole('button',{name:'Save pending proposal',exact:true}).click();
      await page.locator('#r-manager-proposals').getByText('Origin: manual',{exact:false}).waitFor();
      const before=await page.evaluate(async()=> (await (await fetch('/api/research/theme')).json()).result.brief.version);
      await page.getByRole('button',{name:'Analyze feedback with AI',exact:true}).click();
      const ai=page.locator('#r-manager-proposals article').filter({hasText:'Origin: ai'});
      await ai.getByRole('button',{name:'Approve / reject proposal…',exact:true}).waitFor();
      await ai.getByRole('button',{name:'Approve / reject proposal…',exact:true}).click();
      await page.getByLabel('Approving reviewer',{exact:true}).fill('Direction reviewer');
      await page.getByLabel('Decision rationale',{exact:true}).fill('Approved this bounded change after reviewing feedback and the diff.');
      await page.getByRole('button',{name:'Save direction decision',exact:true}).click();
      await ai.getByText('Direction proposal · approved',{exact:true}).waitFor();
      const current=await page.evaluate(async()=> (await (await fetch('/api/research/theme')).json()).result.brief);
      assert.notEqual(current.version,before);
      assert.equal(current.approval_basis,'mock');
      assert.deepEqual(current.signals,['Specific integration planning signals']);
      const manual=page.locator('#r-manager-proposals article').filter({hasText:'Origin: manual'});
      await manual.getByRole('button',{name:'Approve / reject proposal…',exact:true}).click();
      await page.getByLabel('Decision',{exact:true}).selectOption('rejected');
      await page.getByLabel('Decision rationale',{exact:true}).fill('The approved AI proposal already addresses this feedback.');
      await page.getByRole('button',{name:'Save direction decision',exact:true}).click();
      await manual.getByText('Direction proposal · rejected',{exact:true}).waitFor();
      await page.getByLabel('Consultant',{exact:true}).fill('Test consultant');
      await page.getByLabel('Company name',{exact:true}).fill('Synthetic Factory 1');
      await page.getByLabel('Public website domain').fill('synthetic-factory-1.example');
      await page.getByRole('button',{name:'Start research',exact:true}).click();
      await page.locator('#r-status .badge').getByText('completed',{exact:true}).waitFor({timeout:20000});
      const run=await page.evaluate(async()=> (await (await fetch('/api/research/runs')).json()).result[0]);
      assert.equal(run.theme_version,current.version);
      await page.locator('.r-manager').screenshot({path:path.join(output,'workflow-manager.png'),animations:'disabled'});
      await page.reload();
      await page.getByRole('button',{name:'Lead research',exact:true}).click();
      await page.locator('#r-manager-proposals').getByText('Direction proposal · approved',{exact:true}).waitFor();
      assert.deepEqual(errors,[]);
      console.log('P4 browser check passed: scoped feedback, manual pending proposal, one-call AI review, human approval/rejection, persisted versions, and next research uses approved direction.');
    }
    await context.close();
    await backend.stop(); backend=null;
    backend=await server(true);
    const unavailable=await browser.newPage();
    await unavailable.goto(backend.url);
    await unavailable.getByRole('button',{name:'Lead research',exact:true}).click();
    await unavailable.locator('#r-configuration').getByText('Research: Not configured',{exact:true}).waitFor();
    assert.equal(await unavailable.getByRole('button',{name:'Start research',exact:true}).isDisabled(),true);
    await unavailable.screenshot({path:path.join(output,'research-unconfigured.png'),fullPage:true});
    console.log('P2 browser check passed: create/review/reload, source links, escaped input, start/stop/resume, mobile layout, qualification navigation, missing credentials.');
  } finally {
    if(backend) await backend.stop();
    await browser.close();
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
