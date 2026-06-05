<?xml version="1.0" encoding="UTF-8"?>
<!-- Human-readable rendering for the XML sitemaps. Browsers that honour the
     <?xml-stylesheet?> processing instruction show this table; crawlers
     ignore it and read the raw XML. Handles both <sitemapindex> (the index)
     and <urlset> (the chunk files). -->
<xsl:stylesheet version="1.0"
  xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  xmlns:s="http://www.sitemaps.org/schemas/sitemap/0.9">
<xsl:output method="html" encoding="UTF-8" indent="yes"/>

<xsl:template match="/">
  <html lang="zh-Hans">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width,initial-scale=1"/>
    <title>Sitemap · 好奇心日报存档</title>
    <style>
      body { font: 15px/1.6 "PingFang SC","Microsoft YaHei",-apple-system,system-ui,sans-serif; color:#111; margin:0; background:#fff; }
      header { background:#000; color:#fff; padding:18px 24px; }
      header b { color:#ffc81f; }
      .wrap { max-width: 1000px; margin: 0 auto; padding: 18px 24px 60px; }
      .count { color:#555; margin:0 0 1rem; }
      table { border-collapse: collapse; width:100%; font-variant-numeric: tabular-nums; }
      th, td { text-align:left; padding:7px 10px; border-bottom:1px solid #eee; }
      th { color:#888; font-weight:600; font-size:.85rem; border-bottom:2px solid #ddd; }
      td.date { color:#888; white-space:nowrap; width:8rem; }
      a { color:#0353a4; text-decoration:none; word-break:break-all; }
      a:hover { text-decoration:underline; }
    </style>
  </head>
  <body>
    <header><b>Q</b>daily 好奇心日报存档 — Sitemap</header>
    <div class="wrap">
      <xsl:apply-templates/>
    </div>
  </body>
  </html>
</xsl:template>

<!-- Sitemap index: list of child sitemaps -->
<xsl:template match="s:sitemapindex">
  <p class="count">Sitemap index · <xsl:value-of select="count(s:sitemap)"/> 个子地图</p>
  <table>
    <tr><th>Sitemap</th><th>Last modified</th></tr>
    <xsl:for-each select="s:sitemap">
      <tr>
        <td><a href="{s:loc}"><xsl:value-of select="s:loc"/></a></td>
        <td class="date"><xsl:value-of select="s:lastmod"/></td>
      </tr>
    </xsl:for-each>
  </table>
</xsl:template>

<!-- URL set: list of page URLs -->
<xsl:template match="s:urlset">
  <p class="count"><xsl:value-of select="count(s:url)"/> 个网址</p>
  <table>
    <tr><th>URL</th><th>Last modified</th></tr>
    <xsl:for-each select="s:url">
      <tr>
        <td><a href="{s:loc}"><xsl:value-of select="s:loc"/></a></td>
        <td class="date"><xsl:value-of select="s:lastmod"/></td>
      </tr>
    </xsl:for-each>
  </table>
</xsl:template>

</xsl:stylesheet>
