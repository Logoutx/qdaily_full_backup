export const meta = {
  name: 'ab-test-caption',
  description: 'A/B: Haiku vs Sonnet on 15 diverse QDaily images',
  phases: [{ title: 'Caption' }, { title: 'Summary' }],
}

const REPO = '/Users/logoutx/code/qdaily_full_backup'

const IMAGES = [
  { asset: 'assets/5653/29d83df6a35a8478.png',         title: '今年2月，比尔·盖茨要给一个科技网站当客座编辑了', kind: 'banner', type: 'PNG banner' },
  { asset: 'assets/28084/630ac4ee122a4f45.jpg',        title: '好莱坞最大经纪公司之一来中国，腾讯也投了一笔钱', kind: 'banner', type: 'JPG banner' },
  { asset: 'assets/29226/89c5bd51244bda8a.jpeg',       title: '"我每次遇到的都是不打招呼就消失的人" | 22岁，她在想什么？', kind: 'banner', type: 'JPEG portrait' },
  { asset: 'assets/32875/cbcbc60b869af8f4.jpg',        title: '有些文艺书，不躲避也不美化这个世界 | 推荐一些好书', kind: 'banner', type: 'JPG banner' },
  { asset: 'assets/33308/9877c2866204385d.jpg',        title: '今日娱乐：李冰冰拍好莱坞科幻，《三国杀》也要拍电影', kind: 'banner', type: 'JPG banner' },
  { asset: 'assets/36247/df94a5ae40a66ed0.png-w600',   title: '日本人也发明了叠衣服的机器，长得像个金属衣柜', kind: 'body', type: 'PNG product' },
  { asset: 'assets/36433/c2b178a8f89eb5d2.jpg',        title: 'New Balance 去做手表和蓝牙耳机了 | CES 现场报道', kind: 'banner', type: 'JPG product' },
  { asset: 'assets/38314/30141412e27ffead.png-w600',   title: '巴菲特半年花了150亿美元买苹果股票，但苹果可能没有他最爱的"护城河"', kind: 'body', type: 'PNG chart' },
  { asset: 'assets/39552/5be25b9ab537bfc6.jpg-w600',   title: '花瓶就像人物角色，每一个都应有鲜明的个性 | 这个设计了不起', kind: 'body', type: 'JPG design' },
  { asset: 'assets/40054/5249a553655fa364.jpg',        title: '育碧又一次扩张，两家新工作室将专注3A游戏研发', kind: 'banner', type: 'JPG banner' },
  { asset: 'assets/46955/d769e151bdbdc0a6.jpg-w600',   title: '很多城市都想复制毕尔巴鄂的"古根海姆效应"，它当初是怎么成功的？', kind: 'body', type: 'JPG architecture' },
  { asset: 'assets/47934/2eb3653c0d7e2fef.jpg-w600',   title: '在老龄化全球最高的日本，死亡方式反映了老人们的生活方式', kind: 'body', type: 'JPG documentary' },
  { asset: 'assets/52269/869398136bc4f812.png-w600',   title: '在全球化分工的手机行当里，中兴不能买美国芯片和软件意味着什么？', kind: 'body', type: 'PNG diagram' },
  { asset: 'assets/54588/016f1ab9e7ebabdd.png-w600',   title: '老老实实做代工的台积电，在芯片业扮演什么样的角色', kind: 'body', type: 'PNG chart' },
  { asset: 'assets/60162/9be34e4a8d86a0ab.png-w600',   title: '为《纽约客》创作40年的漫画家，笔触为何从温和变得直白而激烈？', kind: 'body', type: 'PNG cartoon' },
]

const makePrompt = (img) =>
  `You write accessibility alt text for photos in an archived 好奇心日报 (QDaily) news site.\n\n` +
  `Article title: ${img.title}\n` +
  `Image kind: ${img.kind} (${img.type})\n` +
  `Image file: ${REPO}/${img.asset}\n\n` +
  `Use the Read tool on the Image file path to look at the image, ` +
  `then write ONE alt text describing what is VISIBLE in the photo, in Simplified Chinese:\n` +
  `- Factual and concrete: name the people/objects/scene/setting actually shown. ~12–40 Chinese characters.\n` +
  `- Use the article title only to disambiguate (who a person likely is, what a chart is about).\n` +
  `- For charts/infographics/screenshots: say it is a chart/界面/截图 and summarize what it shows.\n` +
  `- No surrounding quotes, no "图片/照片显示" filler, no newlines or TAB characters.\n` +
  `- If the image is unreadable, return an empty string.\n\n` +
  `Return ONLY the alt text string, nothing else.`

phase('Caption')
const results = await pipeline(
  IMAGES,
  (img, _, idx) => parallel([
    () => agent(makePrompt(img), { label: `haiku:${idx+1}`, phase: 'Caption', model: 'haiku' }),
    () => agent(makePrompt(img), { label: `sonnet:${idx+1}`, phase: 'Caption', model: 'sonnet' }),
  ]).then(([haiku, sonnet]) => ({ ...img, haiku: haiku || '', sonnet: sonnet || '' }))
)

phase('Summary')
const header = '| # | Type | Article | Haiku | Sonnet |\n|---|---|---|---|---|'
const rows = results.filter(Boolean).map((r, i) =>
  `| ${i+1} | ${r.type} | ${r.title.slice(0, 22)}… | ${r.haiku || '—'} | ${r.sonnet || '—'} |`
).join('\n')
const table = `# Haiku vs Sonnet — 15-image A/B\n\n${header}\n${rows}`
log(table)
return { results: results.filter(Boolean), table }
