"""Publish an explicit, hash-checked translation release. Never calls a model."""
import argparse, hashlib, json, re
from pathlib import Path
import markdown
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape
import render
import translate_qa as qa
from translation_approval import held_ids


def approved(root, manifest):
    tr=root/'data/translations'
    blocked=held_ids(tr)
    for filename in ('defer.json','needs-review.json'):
        p=tr/filename
        if p.exists(): blocked.update(map(str,json.loads(p.read_text())))
    entries=json.loads(manifest.read_text())['files']
    result=[]
    qa.EN=tr/'en';qa.IN=tr/'in'
    for rid,digest in entries.items():
        if not rid.isascii() or not rid.isdigit() or rid in blocked:
            raise ValueError(f'Unpublishable translation: {rid}')
        p=tr/'en'/f'{rid}.json'
        if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
            raise ValueError(f'Translation changed since approval: {rid}')
        record=json.loads(p.read_text());source=json.loads((tr/'in'/f'{rid}.json').read_text())
        hard,_=qa.qa_one(rid)
        if hard or any(not isinstance(record.get(k),str) for k in ('title','excerpt','body')):
            raise ValueError(f'Translation QA failed: {rid}')
        if qa.IMG.findall(record['body'])!=qa.IMG.findall(source['body']) or len(record['body'].strip())<max(200,len(source['body'])*0.5):
            raise ValueError(f'Translation source integrity failed: {rid}')
        result.append({**record,'id':int(rid)})
    return result


def safe_html(text):
    soup=BeautifulSoup(markdown.markdown(text,extensions=['tables','fenced_code']),'html.parser')
    for tag in soup.find_all(['script','iframe','object','embed','form','style','link','meta']):tag.decompose()
    for tag in soup.find_all(True):
        for key in list(tag.attrs):
            value=str(tag.attrs[key]).strip()
            if key.lower().startswith('on') or key.lower() in ('style','srcdoc') or (key.lower() in ('href','src','action') and re.match(r'(?is)^(javascript|vbscript|data):',value)):
                del tag.attrs[key]
    return str(soup)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=Path('.'));ap.add_argument('--out',type=Path,default=Path('public'));args=ap.parse_args()
    root=args.root.resolve();out=args.out.resolve()
    records=approved(root,root/'data/translations/published.json')
    ids={r['id'] for r in records};metadata={}
    for p in sorted((root/'data').glob('articles_extracted_*.jsonl')):
        for line in p.read_text().split('\n'):
            if line.strip():
                r=json.loads(line)
                if r['id'] in ids:metadata[r['id']]=r
    for r in records:
        if r['id'] not in metadata or not (out/'articles'/str(r['id'])/'index.html').exists():
            raise ValueError(f'Original article not published: {r["id"]}')
    env=Environment(loader=FileSystemLoader(root/'site/templates'),autoescape=select_autoescape(['html','xml']))
    env.globals.update(url=lambda p:'/'+str(p).lstrip('/'),static_v=lambda p:hashlib.sha256((root/'site/static'/p).read_bytes()).hexdigest()[:8])
    common={'lang':'en','site_title':'QDaily — English archive','site_description':'English translations of the QDaily archive.','site_url':'https://www.qdaily.org','years':[]}
    render.DEAD_IMAGE_URLS=render.load_dead_image_urls(root/'data/images.jsonl')
    for r in records:
        meta=metadata[r['id']];r['publish_date']=meta.get('publish_date','');r['author']=meta.get('author','')
        r['body_html'],_=render.resolve_body(safe_html(r['body']),meta.get('archive_ts','20190101000000'),r['id'],'local',root/'assets','https://cdn.qdaily.org','/static/placeholder.webp','/',{})
        dest=out/'en/articles'/str(r['id']);dest.mkdir(parents=True,exist_ok=True)
        (dest/'index.html').write_text(env.get_template('english.html').render(**common,article=r,canonical=f'https://www.qdaily.org/en/articles/{r["id"]}/'))
    search=out/'en/search';search.mkdir(parents=True,exist_ok=True)
    (search/'index.html').write_text(env.get_template('english.html').render(**common,search=True,canonical='https://www.qdaily.org/en/search/'))
    records.sort(key=lambda r:(r['publish_date'],r['id']),reverse=True)
    pages=[records[i:i+100] for i in range(0,len(records),100)]
    for i,items in enumerate(pages,1):
        relative='en/' if i==1 else f'en/page/{i}/';dest=out/relative;dest.mkdir(parents=True,exist_ok=True)
        (dest/'index.html').write_text(env.get_template('english.html').render(**common,articles=items,total=len(records),page=i,pages=len(pages),canonical='https://www.qdaily.org/'+relative))
    xml='<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join(f'<url><loc>https://www.qdaily.org/en/articles/{r["id"]}/</loc></url>' for r in records)+'</urlset>'
    (out/'sitemap-en.xml').write_text(xml)
    sitemap=out/'sitemap.xml';text=sitemap.read_text();text=text.replace('<sitemap><loc>https://www.qdaily.org/sitemap-en.xml</loc></sitemap>','');text=text.replace('</sitemapindex>','<sitemap><loc>https://www.qdaily.org/sitemap-en.xml</loc></sitemap></sitemapindex>');sitemap.write_text(text)
    (out/'en/manifest.json').write_text(json.dumps({'count':len(records),'ids':[r['id'] for r in records]},indent=2))
    print(f'Published build: {len(records)} translations; {len(pages)} index pages; no model calls.')
    return 0

if __name__=='__main__':raise SystemExit(main())
