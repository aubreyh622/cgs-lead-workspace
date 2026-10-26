/* Launch the real offline entry point; all browser traffic stays on loopback. */
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {mkdir,writeFile}=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require(process.env.CGS_TEST_PLAYWRIGHT||'playwright');
const output=process.env.CGS_TEST_OUTPUT;
if(!output||!process.env.CGS_TEST_PYTHON) throw Error('Set test runtime and output paths.');
const root=path.resolve(__dirname,'../..');

(async()=>{
  await mkdir(output,{recursive:true});
  const backend=spawn(process.env.CGS_TEST_PYTHON,['-B','demo.py','--port','0','--workspace-dir',path.join(output,'demo-data')],
    {cwd:root,windowsHide:true,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
  let browser,diagnostic='';
  backend.stderr.on('data',data=>diagnostic+=data);
  try {
    const url=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(Error('Demo startup timed out: '+diagnostic)),30000);
      let text='';
      backend.stdout.on('data',data=>{
        text+=data;
        if(text.includes('\n')) {clearTimeout(timer);try{resolve(JSON.parse(text.split('\n')[0]).url);}catch(e){reject(e);}}
      });
      backend.on('exit',code=>{clearTimeout(timer);reject(Error('Demo exited '+code+': '+diagnostic));});
    });
    browser=await chromium.launch({headless:true,executablePath:process.env.CGS_TEST_BROWSER});
    const context=await browser.newContext({viewport:{width:1440,height:1080}});
    const errors=[];
    await context.route('**/*',route=>['localhost','127.0.0.1'].includes(new URL(route.request().url()).hostname)?route.continue():route.abort());
    const page=await context.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    async function api(endpoint){const response=await context.request.get(url+endpoint);assert.ok(response.ok());const data=await response.json();return data.result??data;}
    await page.goto(url);
    await page.getByText('OFFLINE SYNTHETIC DEMO',{exact:true}).waitFor();
    const boot=await api('api/bootstrap');
    assert.equal(boot.offlineDemo,true);
    assert.equal(boot.cases.length,9);
    await page.getByRole('button',{name:'Lead research',exact:true}).click();
    await page.getByText('Research: Offline synthetic fixtures',{exact:true}).waitFor();
    await page.locator('#r-results .sample-label').getByText('SYNTHETIC SAMPLE',{exact:true}).waitFor();
    assert.equal(await page.locator('#r-jobs .r-job').count(),4);
    assert.equal(await page.locator('#r-opportunities [data-opportunity]').count(),4);
    await page.screenshot({path:path.join(output,'offline-research.png')});
    const opportunities=await api('api/research/opportunities');
    const unknown=boot.cases.find(c=>c.researchHandoff&&c.contact===null);
    assert.equal(unknown.stage,'Not contacted');
    assert.equal(unknown.result.coverage,0);
    const unknownOpportunity=opportunities.find(o=>o.opportunity.opportunity_id===unknown.researchHandoff.receipt.opportunity_id);
    await page.locator(`[data-opportunity="${unknownOpportunity.opportunity.opportunity_id}"]`).getByRole('button',{name:'Open qualification case',exact:true}).click();
    await page.getByText('Research handoff background',{exact:true}).waitFor();
    await page.getByText('OFFLINE SYNTHETIC DEMO',{exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'received-unknown-contact.png')});
    await page.getByRole('button',{name:'Lead research',exact:true}).click();
    await page.getByRole('button',{name:'Use Synthetic Factory 1',exact:true}).click();
    assert.equal(await page.getByLabel('Company name',{exact:true}).inputValue(),'Synthetic Factory 1');
    await page.getByRole('button',{name:'Start research',exact:true}).click();
    await page.locator('#r-status .badge').getByText('completed',{exact:true}).waitFor();
    await page.waitForFunction(()=>document.querySelectorAll('#r-jobs .r-job').length===5);
    await page.getByRole('button',{name:'Save an opportunity…',exact:true}).click();
    await page.getByLabel('Opportunity title',{exact:true}).fill('Interactive synthetic integration opportunity');
    await page.getByLabel('Team / business unit (optional)').fill('Fictional plant integration team');
    await page.getByRole('button',{name:'Save opportunity',exact:true}).click();
    const newOpportunity=page.locator('[data-opportunity]').filter({has:page.getByRole('heading',{name:'Interactive synthetic integration opportunity',exact:true})});
    await newOpportunity.getByRole('button',{name:'Record human review…',exact:true}).click();
    await page.getByLabel('Reviewer / consultant').fill('Demo consultant');
    await page.getByLabel('Decision',{exact:true}).selectOption('worth_exploring');
    await page.getByLabel('Reason',{exact:true}).selectOption('relevant_signal');
    await page.getByLabel('Feedback applies to').selectOption('theme');
    await page.getByLabel('I checked the cited facts',{exact:false}).check();
    await page.getByRole('button',{name:'Save review',exact:true}).click();
    await newOpportunity.locator('.badge').getByText('Worth exploring',{exact:true}).waitFor();
    await page.getByLabel('Manager consultant context').fill('Demo consultant');
    await page.getByRole('button',{name:'Load manager context',exact:true}).click();
    await page.getByRole('button',{name:'Simulate feedback review',exact:true}).click();
    await page.getByText('No direction change was proposed.',{exact:true}).waitFor();
    const before=(await api('api/research/theme')).brief.version;
    await page.getByRole('button',{name:'Create manual proposal…',exact:true}).click();
    await page.getByLabel('Direction patch (JSON)').fill(JSON.stringify({signals:['Integration sequencing and data governance planning']}));
    await page.getByLabel('Feedback-based rationale').fill('Synthetic workshop exercise: investigate integration sequencing before implementation demand.');
    await page.getByRole('button',{name:'Save pending proposal',exact:true}).click();
    await page.getByRole('button',{name:'Approve / reject proposal…',exact:true}).waitFor();
    assert.equal((await api('api/research/theme')).brief.version,before);
    await page.getByRole('button',{name:'Approve / reject proposal…',exact:true}).click();
    await page.getByLabel('Approving reviewer').fill('Demo direction reviewer');
    await page.getByLabel('Decision rationale').fill('Explicit synthetic approval for the interactive browser rehearsal.');
    await page.getByRole('button',{name:'Save direction decision',exact:true}).click();
    await page.locator('#r-dialog').waitFor({state:'hidden'});
    const after=(await api('api/research/theme')).brief.version;
    assert.notEqual(before,after);
    await page.getByRole('button',{name:'Approve / reject proposal…',exact:true}).waitFor({state:'hidden'});
    await page.locator('.r-manager').screenshot({path:path.join(output,'offline-manager.png')});
    await page.getByRole('button',{name:'Use Synthetic Factory 2',exact:true}).click();
    await page.getByRole('button',{name:'Start research',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('#r-jobs .r-job').length===6);
    await page.locator('#r-status .badge').getByText('completed',{exact:true}).waitFor();
    assert.ok((await page.locator('#r-status').textContent()).includes(after));
    const runs=await api('api/research/runs');
    assert.equal(runs.length,6);
    assert.ok(runs.every(r=>r.mode==='synthetic'));
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.querySelector('#sidebar').getBoundingClientRect().right<=1);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:path.join(output,'offline-mobile.png')});
    assert.deepEqual(errors,[]);
    const report={passed:true,seeded_tasks:4,interactive_tasks:2,synthetic:true,external_browser_requests_allowed:false,
      verified:['offline banner','source provenance','unknown-contact receiving case','interactive research and review','simulated no-change analysis','pending proposal does not apply','human direction approval','next-task version','390px layout'],page_errors:errors};
    await writeFile(path.join(output,'browser-report.json'),JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify(report));
  } finally {
    if(browser) await browser.close();
    if(backend.exitCode===null) {const exited=new Promise(resolve=>backend.once('exit',resolve));backend.kill();await exited;}
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
