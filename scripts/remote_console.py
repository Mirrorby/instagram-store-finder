"""Fixed-scope GitHub/Cloudflare maintenance via the user's Chrome login.
No cookie/token export; secret binding values are never requested or logged.
"""
import sys,json,base64,subprocess,mimetypes,hashlib,re
from pathlib import Path
from store_finder.browser import BrowserProfiles
ACCOUNT='6cba2ecdc192570aba36e3a6d2765924'
BASE='/api/v4/accounts/'+ACCOUNT+'/workers/scripts/catalog-factory'
ROOT=Path(__file__).resolve().parents[1]
MODE=sys.argv[1] if len(sys.argv)>1 else 'inspect'
with BrowserProfiles(attach_chrome=True,cdp_endpoint='ws://127.0.0.1:9222/devtools/browser') as browser:
    github=next((p for p in browser.context.pages if 'github.com/Mirrorby/instagram-store-finder/actions/workflows/cloud.yml' in p.url),None)
    cloud=next((p for p in browser.context.pages if 'dash.cloudflare.com/'+ACCOUNT in p.url),None)
    if MODE=='disable-apify':
        assert github is not None
        if 'This workflow was disabled' not in github.locator('body').inner_text():
            github.locator('button[data-variant="default"]:has(svg.octicon-kebab-horizontal)').first.click()
            print('MENU',github.locator('[role="menu"]').inner_text())
            github.get_by_text('Disable workflow',exact=True).click()
            github.wait_for_timeout(2000)
            print('DIALOGS',github.locator('[role="dialog"]').all_text_contents())
        print('GITHUB',github.locator('body').inner_text()[:6500])
    elif MODE=='confirm-apify':
        assert github is not None
        if 'Disable workflow?' in github.locator('body').inner_text():
            github.get_by_role('button',name='Disable workflow',exact=True).click()
            github.wait_for_timeout(2500)
        print('GITHUB',github.locator('body').inner_text()[:6500])
    elif MODE=='cloud-backup':
        assert cloud is not None
        backup=cloud.evaluate("""async base=>{
            const r=await fetch(base);if(!r.ok)throw new Error('Worker backup failed: '+r.status);
            return {contentType:r.headers.get('content-type'),body:await r.text()};
        }""",BASE)
        directory=ROOT/'output/desk/deployment-backup';directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        path=directory/'previous-worker.multipart';path.write_text(backup['body']);path.chmod(0o600)
        print('BACKUP',backup['contentType'],len(backup['body']))
    elif MODE=='publish-cloud':
        assert cloud is not None
        assert (ROOT/'output/desk/deployment-backup/previous-worker.multipart').is_file(), 'Backup required'
        code=(ROOT.parent/'delokay/dist/worker.mjs').read_text()
        assert 'STOREFRONT_LOCALES' in code and 'supportedLanguages' in code
        result=cloud.evaluate("""async ({base,code})=>{
            const current=await fetch(base+'/settings');const data=await current.json();
            if(!current.ok||!data.success)throw new Error('Cannot read current settings');
            const settings=data.result;
            const metadata={main_module:'worker.mjs',keep_assets:true,keep_bindings:['secret_text'],bindings:settings.bindings.filter(b=>b.type!=='secret_text')};
            for(const key of ['compatibility_date','compatibility_flags','usage_model','placement','tags','tail_consumers','logpush','observability'])if(settings[key]!==undefined)metadata[key]=settings[key];
            metadata.annotations={'workers/message':'Delokay personalized demos: 10 storefront languages and visible contact buttons; based on 2a99dca'};
            window.__delokayBeforeBindingNames=settings.bindings.map(b=>b.name+':'+b.type).sort();
            const form=new FormData();
            form.set('metadata',new Blob([JSON.stringify(metadata)],{type:'application/json'}));
            form.set('worker.mjs',new Blob([code],{type:'application/javascript+module'}),'worker.mjs');
            const r=await fetch(base,{method:'PUT',body:form});const result=await r.json();
            return {status:r.status,success:result.success,errors:result.errors,messages:result.messages,id:result.result?.id,etag:result.result?.etag,modified_on:result.result?.modified_on,version_id:result.result?.version_id,deployment_id:result.result?.deployment_id};
        }""",{'base':BASE,'code':code})
        path=ROOT/'output/desk/deployment-backup/publish-result.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2));path.chmod(0o600)
        print('DEPLOY',json.dumps(result,ensure_ascii=False))
        if not result.get('success'):raise SystemExit(1)
    elif MODE=='verify-live':
        assert cloud is not None and github is not None
        bindings=cloud.evaluate("""async base=>{
            const r=await fetch(base+'/settings');const data=await r.json();if(!data.success)throw new Error('Settings read failed');
            const after=data.result.bindings.map(b=>b.name+':'+b.type).sort();
            return {preserved:JSON.stringify(after)===JSON.stringify(window.__delokayBeforeBindingNames),bindings:after};
        }""",BASE)
        assert bindings['preserved'],bindings
        print('BINDINGS',json.dumps(bindings))
        github.reload(wait_until='domcontentloaded');github.wait_for_timeout(1500)
        assert 'This workflow was disabled manually.' in github.locator('body').inner_text()
        discovery=browser.context.new_page()
        try:
            discovery.goto('https://github.com/Mirrorby/instagram-store-finder/actions/workflows/discover.yml',wait_until='domcontentloaded')
            discovery.wait_for_timeout(2000)
            assert 'This workflow was disabled' not in discovery.locator('body').inner_text()
            assert discovery.get_by_role('button',name='Run workflow',exact=True).is_visible()
            print('GITHUB VERIFIED: Apify disabled; discovery enabled')
        finally:discovery.close()
        page=browser.context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        origin='https://catalog-factory.nikita-karpof.workers.dev'
        try:
            prices={'ru':'Цена по запросу','en':'Price on request','es':'Precio a consultar','pt':'Preço sob consulta','fr':'Prix sur demande','it':'Prezzo su richiesta','tr':'Fiyat için iletişime geçin','pl':'Cena na zapytanie','ja':'価格はお問い合わせください','de':'Preis auf Anfrage'}
            for language in prices:
                response=page.request.get(origin+'/api/templates/minimal?lang='+language)
                assert response.ok, response.status
                data=response.json();assert data['store']['settings']['language']==language
                page.goto(origin+'/templates/minimal?lang='+language,wait_until='domcontentloaded')
                page.locator('.card-name').first.wait_for()
                assert page.locator('html').get_attribute('lang')==language
            slug='ig-the-jewelry-shop-cd6f80ba'
            response=page.request.get(origin+'/api/stores/'+slug);assert response.ok
            data=response.json();assert len(data['products'])==6
            photos=[url for p in data['products'] for url in p['photos']];assert len(photos)==11
            for url in photos:
                result=page.request.get(origin+url if url.startswith('/') else url);assert result.ok,(url,result.status)
            page.goto(origin+'/s/'+slug,wait_until='domcontentloaded');page.locator('.card-name').first.wait_for()
            for channel in ['tg','wa','ig']:assert page.locator('#hero-'+channel).is_visible()
            assert page.locator('#hero-ig').get_attribute('href')=='https://www.instagram.com/the.jewelry.shop/'
            assert page.locator('#hero-tg').get_attribute('aria-disabled')=='true'
            assert not errors,errors
            page.screenshot(path=str(ROOT/'output/desk/live-demo.png'))
            print('LIVE VERIFIED',json.dumps({'languages':list(prices),'store_language':data['store']['settings']['language'],'products':len(data['products']),'photos':len(photos),'contacts':'3 visible; Instagram linked','js_errors':errors},ensure_ascii=False))
        finally:page.close()
    elif MODE=='fix-jewelry-language':
        assert cloud is not None
        from store_finder.personal_demos import save,write_preview,contact_draft,private_write
        from store_finder.desk_store import DeskStore
        directory=ROOT/'output/demos-v2/the.jewelry.shop';state_path=directory/'publish.json';state=json.loads(state_path.read_text())
        backup=ROOT/'output/desk/deployment-backup/jewelry-publish-before.json';private_write(backup,state_path.read_text())
        result=cloud.evaluate("""async ({base,account,snapshot})=>{
            const settings=await (await fetch(base+'/settings')).json();if(!settings.success)throw new Error('Cannot read binding metadata');
            const db=settings.result.bindings.find(b=>b.name==='DB'&&b.type==='d1');if(!db)throw new Error('Missing DB');
            const endpoint='/api/v4/accounts/'+account+'/d1/database/'+db.id+'/query';
            const query=async (sql,params)=>{const r=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sql,params})});const data=await r.json();if(!r.ok||!data.success||!data.result?.[0]?.success)throw new Error('D1 query failed: '+JSON.stringify(data.errors));return data.result[0];};
            const id='0180b9da-0a1a-406e-a9f9-350d0a9231b5',slug='ig-the-jewelry-shop-cd6f80ba';
            const read=await query('SELECT name,settings,version FROM stores WHERE id=? AND slug=? AND status=? AND owner_id IS NULL',[id,slug,'demo']);
            if(read.results.length!==1)throw new Error('Demo ownership/status changed');
            const before=read.results[0],cfg=JSON.parse(before.settings);
            const stable=v=>JSON.stringify(v, function(k,value){if(value&&typeof value==='object'&&!Array.isArray(value))return Object.fromEntries(Object.keys(value).sort().map(k=>[k,value[k]]));return value;});
            if(before.name!==snapshot.name||before.version!==snapshot.version||stable(cfg)!==stable(snapshot.settings))throw new Error('Demo was edited; stopped');
            if(cfg.language!=='ru'||cfg.instagram!=='the.jewelry.shop'||Object.keys(cfg.texts||{}).length)throw new Error('Unexpected demo copy');
            const next={...cfg,language:'en',deliveryMethods:cfg.deliveryMethods.map(v=>({'Доставка':'Delivery','Самовывоз':'Pickup'}[v]||v))};
            const updated=await query('UPDATE stores SET settings=?,version=version+1 WHERE id=? AND slug=? AND status=? AND owner_id IS NULL AND version=? AND settings=? RETURNING name,settings,version',[JSON.stringify(next),id,slug,'demo',String(before.version),before.settings]);
            if(updated.results.length!==1)throw new Error('Concurrent edit; stopped');
            return {before,after:updated.results[0]};
        }""",{'base':BASE,'account':ACCOUNT,'snapshot':state['remote']})
        private_write(ROOT/'output/desk/deployment-backup/jewelry-language-change.json',json.dumps(result,ensure_ascii=False,indent=2))
        state['remote']={**result['after'],'settings':json.loads(result['after']['settings'])};save(state_path,state)
        bundle=json.loads((directory/'bundle.json').read_text());bundle['store']['settings']['language']='en';save(directory/'bundle.json',bundle);write_preview(bundle,directory)
        draft=contact_draft(bundle,state['store']['url']);private_write(directory/'first-contact.txt',draft+'\n')
        DeskStore(ROOT/'output/desk').update('the.jewelry.shop',message=draft)
        print('JEWELRY UPDATED',{'language':'en','version':state['remote']['version']})
    elif MODE=='github-upload-info':
        for repo,branch in [('f1demo','feat/instagram-personal-demos'),('instagram-store-finder','feat/personalized-demos')]:
            page=browser.context.new_page()
            page.goto('https://github.com/Mirrorby/'+repo+'/upload/'+branch,wait_until='domcontentloaded');page.wait_for_timeout(2000)
            print('UPLOAD',page.url,page.locator('body').inner_text()[-6000:])
            print('FILE INPUTS',page.locator('input[type=file]').evaluate_all('(els)=>els.map(e=>({id:e.id,name:e.name,multiple:e.multiple,directory:e.webkitdirectory,accept:e.accept}))'))
    elif MODE=='github-stage':
        repo=sys.argv[2];assert repo in {'f1demo','instagram-store-finder'}
        branch='feat/instagram-personal-demos' if repo=='f1demo' else 'feat/personalized-demos'
        folder=ROOT.parent/'delokay' if repo=='f1demo' else ROOT
        page=next(p for p in browser.context.pages if 'github.com/Mirrorby/'+repo+'/upload/' in p.url)
        expected=subprocess.check_output(['git','rev-parse','HEAD^'],cwd=folder,text=True).strip()
        response=page.request.get('https://api.github.com/repos/Mirrorby/'+repo+'/git/ref/heads/'+branch)
        assert response.ok, response.status
        actual=response.json()['object']['sha'];assert actual==expected,('Branch changed remotely',actual,expected)
        names=subprocess.check_output(['git','diff-tree','--no-commit-id','--name-only','-r','-z','HEAD'],cwd=folder,text=True).strip('\0').split('\0')
        payload=[]
        for name in names:
            assert not name.startswith(('sources/','output/','.env','.git/'))
            payload.append({'path':name,'data':base64.b64encode((folder/name).read_bytes()).decode(),'type':mimetypes.guess_type(name)[0] or 'application/octet-stream'})
        page.reload(wait_until='domcontentloaded')
        page.locator('#upload-manifest-files-input').wait_for(state='attached')
        page.locator('#upload-manifest-files-input').evaluate("""(input,files)=>{
            const transfer=new DataTransfer();for(const item of files){
                const bytes=Uint8Array.from(atob(item.data),c=>c.charCodeAt(0));
                const file=new File([bytes],item.path,{type:item.type});
                Object.defineProperty(file,'webkitRelativePath',{value:item.path});transfer.items.add(file);
            }
            input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));
        }""",payload)
        page.wait_for_timeout(12000)
        print('STAGED',repo,len(names),page.locator('body').inner_text()[-11000:])
        print('COMMIT FIELDS',page.locator('input,textarea').evaluate_all('(els)=>els.filter(e=>["text","textarea","radio"].includes(e.type)).map(e=>({id:e.id,name:e.name,type:e.type,value:e.type==="radio"?e.value:null}))'))
    elif MODE=='github-commit':
        repo=sys.argv[2];assert repo in {'f1demo','instagram-store-finder'}
        branch='feat/instagram-personal-demos' if repo=='f1demo' else 'feat/personalized-demos'
        folder=ROOT.parent/'delokay' if repo=='f1demo' else ROOT
        page=next(p for p in browser.context.pages if 'github.com/Mirrorby/'+repo+'/upload/' in p.url)
        names=subprocess.check_output(['git','diff-tree','--no-commit-id','--name-only','-r','-z','HEAD'],cwd=folder,text=True).strip('\0').split('\0')
        for _ in range(100):
            text=page.locator('body').inner_text()
            if not re.search(r'Uploading \d+ of \d+',text) and all(name in text for name in names):break
            page.wait_for_timeout(500)
        else:raise ValueError('Uploads still running or missing files')
        message='Support ten storefront languages and visible demo contact buttons' if repo=='f1demo' else 'Add local store desk and multilingual personalized demos without Apify'
        page.locator('#commit-summary-input').fill(message)
        page.locator('#commit-description-textarea').fill('Published and verified on 2026-10-15. Existing public demo retains 6 products and 11 photos. Apify workflow disabled; daily discovery remains enabled.')
        page.locator('input[name="commit-choice"][value="direct"]').check()
        page.get_by_role('button',name='Commit changes',exact=True).click()
        page.wait_for_timeout(3500)
        print('COMMITTED',repo,page.url,page.locator('body').inner_text()[-3000:])
    elif MODE=='github-verify-source':
        page=github
        for repo,branch,folder in [('f1demo','feat/instagram-personal-demos',ROOT.parent/'delokay'),('instagram-store-finder','feat/personalized-demos',ROOT)]:
            api='https://api.github.com/repos/Mirrorby/'+repo+'/git/'
            response=page.request.get(api+'ref/heads/'+branch);assert response.ok
            sha=response.json()['object']['sha']
            response=page.request.get(api+'commits/'+sha);assert response.ok
            commit=response.json()
            response=page.request.get(api+'trees/'+commit['tree']['sha']+'?recursive=1');assert response.ok
            tree={item['path']:item for item in response.json()['tree']}
            names=subprocess.check_output(['git','diff-tree','--no-commit-id','--name-only','-r','-z','HEAD'],cwd=folder,text=True).strip('\0').split('\0')
            for name in names:
                data=(folder/name).read_bytes();expected=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
                assert tree.get(name,{}).get('sha')==expected,('Source mismatch',repo,name)
            print('SOURCE VERIFIED',repo,sha,len(names),'files')
    elif MODE=='cloud-config':
        assert cloud is not None
        result=cloud.evaluate('''async base=>{
          const r=await fetch(base+'/settings'); const data=await r.json();
          if(!r.ok||!data.success)return {status:r.status,success:data.success,errors:data.errors};
          window.__delokayCurrentSettings=data.result;
          return {status:r.status,keys:Object.keys(data.result),compatibility_date:data.result.compatibility_date,compatibility_flags:data.result.compatibility_flags,assets:data.result.assets,bindings:data.result.bindings.map(b=>({name:b.name,type:b.type})),observability:data.result.observability};
        }''',BASE)
        print('CLOUD CONFIG',json.dumps(result,ensure_ascii=False))
    else:
        for p in [github,cloud]:
            if p:print(json.dumps({'url':p.url,'text':p.locator('body').inner_text()[-7000:]},ensure_ascii=False))
