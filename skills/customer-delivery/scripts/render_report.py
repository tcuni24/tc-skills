#!/usr/bin/env python3
"""把 JSON 内容规范渲染成离线可读的客户交付 HTML 报告。

用法：
  render_report.py content.json -o <交付目录>/report.html [--logo brand.png]

内容规范见 references/content-spec.md。渲染器只做排版、编号和格式化，
不重新统计任何业务数字；所有数值由上游内容层给出。
图片使用相对路径（默认 plot/xxx.png），HTML 必须与 plot/ 目录一起交付。
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import sys
from pathlib import Path

CSS = r"""
:root{--ink:#292d2c;--muted:#626a68;--quiet:#87908d;--paper:#fff;--canvas:#dfe3e2;--line:#d3d9d7;--soft:#f5f8f7;
--blue:#007f78;--blue-deep:#075f5a;--teal:#00a69c;--coral:#b44b46;--amber:#9a7414;--green:#287a57;--red:#a9403b;
--green-soft:#eef8f2;--amber-soft:#fff7ea;--red-soft:#fff2f1;
--font-sans:"Helvetica Neue",Arial,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
--font-mono:"SFMono-Regular",Consolas,"Liberation Mono",monospace}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;color:var(--ink);font-family:var(--font-sans);line-height:1.58;background:var(--canvas);-webkit-font-smoothing:antialiased;-webkit-print-color-adjust:exact;print-color-adjust:exact}
img{max-width:100%}button{font:inherit}a{color:var(--blue-deep)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:2mm;text-align:left;vertical-align:top}
th{background:#fff;border-top:1.5px solid #333;border-bottom:1px solid #333;font-size:8.5px}
td{font-size:8.5px}.number{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.report{width:210mm;max-width:100%;margin:18px auto;background:#fff;box-shadow:0 2px 12px rgba(0,0,0,.15)}
.docnav{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:0 16px;padding:2.5mm 18mm;background:rgba(255,255,255,.94);backdrop-filter:blur(6px);border-bottom:1px solid #ccc;font-size:9px;letter-spacing:.04em}
.docnav a{color:#666;text-decoration:none;padding:1.2mm 0;border-bottom:1.5px solid transparent;display:inline-flex;align-items:baseline;gap:4px}
.docnav a em{font-style:normal;font-size:8px;font-weight:800;color:var(--blue)}
.docnav a:hover,.docnav a.is-current{color:var(--blue);border-bottom-color:var(--blue)}
.docnav .docnav-print{margin-left:auto;color:var(--blue);border:1px solid var(--blue);background:#fff;padding:2px 8px;font-size:8px;letter-spacing:.06em;text-transform:uppercase;cursor:pointer}
.cover{position:relative;padding:22mm 18mm 12mm;background:#fff}
.cover-logo{display:block;margin:0 0 7mm}.cover-logo img{display:block;height:11mm;width:auto}
.brandline{color:var(--blue);font-size:9px;letter-spacing:.2em;text-transform:uppercase}
.cover h1{font-size:38px;line-height:1.08;font-weight:800;color:#242424;letter-spacing:-.02em;margin:8mm 0 4mm}
.subtitle{font-size:14px;color:#555;margin:0}
.b-intro{margin-top:8mm;padding:4mm 6mm;background:var(--soft);border-left:3px solid var(--blue)}
.b-eyebrow{font-size:9px;letter-spacing:.12em;color:var(--blue);font-weight:600}
.b-intro h2{font-size:16px;line-height:1.5;margin:2mm 0 2mm;color:var(--ink)}
.b-intro p{font-size:9.5px;color:var(--muted);margin:0;line-height:1.7}
.meta{display:grid;grid-template-columns:repeat(4,1fr);margin-top:8mm;border-top:1px solid #777;border-bottom:1px solid #ccc}
.meta-item{border-right:1px solid #ddd;padding:6px 8px}.meta-item:last-child{border-right:0}
.meta-item span{display:block;font-size:8px;text-transform:uppercase;letter-spacing:.06em;color:#666}
.meta-item strong{display:block;font-size:9px;color:#333;overflow-wrap:anywhere}
.twist-metrics{display:grid;grid-template-columns:repeat(4,1fr);margin:0 18mm 10mm;border-bottom:1px solid #bbb}
.twist-metric{padding:4mm 3mm 4.5mm;border-right:1px solid #ccc;border-top:2px solid var(--blue)}
.twist-metric:last-child{border-right:0}
.twist-metric b{display:block;font-size:19px;color:var(--blue);line-height:1.1;font-variant-numeric:tabular-nums}
.twist-metric span{display:block;margin-top:3px;font-size:8px;color:#666}
.twist-metric i{display:block;margin-top:2.5px;font-style:normal;font-size:8.5px;line-height:1.5;color:#777}
.twist-metric.tm-watch{border-top-color:var(--amber)}.twist-metric.tm-watch b{color:var(--amber)}
.content{padding:0 18mm 18mm}
.section{padding-top:9mm;scroll-margin-top:46px}.section+.section{margin-top:6mm;border-top:1px solid #777}
.section-head{display:flex;align-items:flex-start;gap:3mm;margin-bottom:3mm}
.section-no{color:var(--blue);font-size:9px;font-weight:800;padding-top:2px}
.section-title h2{font-size:18px;line-height:1.25;font-weight:800;color:#292929;margin:0}
.section-title p{margin:0;font-size:8px;text-transform:uppercase;letter-spacing:.13em;color:var(--blue)}
.lead{font-size:10px;margin:2mm 0 4mm;color:var(--muted)}
.panel{margin-top:5mm}.panel h3{font-size:13px;border-bottom:1px solid #777;padding-bottom:2mm;margin:0 0 3mm}
.panel h4{font-size:10px;color:var(--blue);margin:4mm 0 1.5mm}
p{font-size:9.5px;margin:0 0 2.5mm}
.conclusion,.watchbox{padding:4mm;margin-bottom:3mm}
.conclusion{border-left:3px solid var(--blue);background:#f2f8f7}
.conclusion-label{font-size:8px;color:var(--blue);letter-spacing:.06em}
.conclusion h3{font-size:16px;color:#333;margin:1mm 0 2mm}
.conclusion p,.watchbox p{font-size:9.5px;margin:0}
.watchbox{border:1px solid #bbb;background:#fff}.watchbox h3{font-size:11px;color:#333;margin:0 0 1mm}
.table-wrap{width:100%;overflow-x:auto;margin-top:3mm}
.table-caption{font-size:8px;color:#555;margin:1.5mm 0 0}
.note{margin-top:4mm;font-size:8.5px;padding:3mm;border-left:2px solid var(--blue);background:#f4f8f7}
.note.warning{border-left-color:var(--amber);background:#faf7ee}
.steps{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid #777;border-bottom:1px solid #777;margin-top:3mm}
.step{border-right:1px solid #ccc;padding:3mm}.step:last-child{border-right:0}
.step small{font-family:var(--font-mono);font-size:8px;color:var(--blue)}
.step b{display:block;font-size:9px;color:#333;margin:1mm 0}.step span{display:block;font-size:8px;color:var(--muted)}
.specs{display:grid;grid-template-columns:1fr;gap:1.5mm;margin-top:3mm}
.spec{border-left:2px solid var(--blue);background:#f5f8f8;padding:2mm 3mm;font-size:8.5px}
.spec b{color:var(--blue-deep)}
.grid-2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4mm;margin-top:2mm}.grid-2>*{min-width:0}
.hits-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4mm;margin-top:4mm}
.hits-grid.hits-grid-4{grid-template-columns:repeat(4,minmax(0,1fr))}.hits-grid.hits-grid-2{grid-template-columns:repeat(2,minmax(0,1fr))}
.hits-card h4{margin:0 0 2mm;font-size:11px;border-bottom:1px solid #777;padding-bottom:1.5mm}
.hits-card p{margin:0;font-size:9px}
.hits-card.good h4{color:#2b6f47}.hits-card.warn h4{color:#8a580f}.hits-card.bad h4{color:#98413b}.hits-card.neutral h4{color:#4c5351}
.b-bars{display:grid;grid-template-columns:1fr 1fr;gap:6mm;margin:4mm 0}
.b-bar-title{font-size:9px;display:flex;justify-content:space-between;margin-bottom:2mm}.b-bar-title strong{color:var(--blue-deep)}
.b-track{display:flex;height:12px;overflow:hidden;background:var(--soft)}.b-track span{display:block}
.b-legend{display:flex;gap:12px;flex-wrap:wrap;font-size:8px;color:var(--muted);margin-top:2mm}
.b-legend i{display:inline-block;width:7px;height:7px;margin-right:5px}
.c-green{background:var(--blue)}.c-teal{background:#79b7ad}.c-amber{background:#d5b665}.c-red{background:var(--coral)}.c-gray{background:#c3cbc8}
.chart-card{margin-top:9mm;break-inside:avoid}
.chart-copy{padding:0 0 3mm;border-bottom:1px solid #888}.chart-copy h3{font-size:13px;margin:0 0 2mm}
.chart-copy-text{display:grid;grid-template-columns:1fr 1fr;gap:0 7mm}
.chart-copy-text p{margin:0;font-size:8.8px}
.chart-copy-text p:first-child{padding-right:6mm;border-right:1px solid var(--line)}
.chart-copy-text p strong{display:block;font-size:7.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--blue);margin:0 0 1.6mm}
.chart-image{padding:4mm 0 2mm}.chart-image img{display:block;margin:auto;max-height:200mm}
.chart-caption{font-size:8px;color:#555;padding:2mm 0 0}
.placeholder-box{display:none;background:var(--soft);border:1px dashed #aeb9b6;padding:6mm;text-align:center;font-size:9px;color:var(--muted)}
.placeholder-box code{font-family:var(--font-mono)}
.dictionary td:first-child{width:220px;color:var(--blue);font-family:var(--font-mono);font-weight:700;word-break:break-word}
details{margin-top:4mm;border:1px solid var(--line);padding:0 4mm}
details summary{cursor:pointer;font-size:11px;font-weight:700;padding:3mm 0;color:var(--blue-deep)}
details[open] summary{border-bottom:1px solid var(--line);margin-bottom:3mm}
details>*:last-child{margin-bottom:4mm}
.badge{display:inline-block;font-size:7.5px;font-weight:800;padding:0 3px}
.badge.green{background:#e7f5ec;color:#276c45}.badge.blue{background:#eaf3f8;color:#23688b}.badge.amber{background:#fff1d9;color:#91590b}.badge.red{background:#fde9e7;color:#a23b36}.badge.gray{background:#eef0ef;color:#4c5351}
.footer{display:flex;align-items:flex-start;justify-content:space-between;border-top:1px solid #777;color:#555;padding:3mm 18mm;font-size:8px}
.footer strong{color:#333}.footer .right{text-align:right}
.footer .foot-brand{display:flex;align-items:center;gap:3mm}.footer .footer-logo img{display:block;height:6mm;width:auto;opacity:.75}
ul,ol{font-size:9.5px;margin:0 0 2.5mm;padding-left:5mm}li{margin-bottom:1mm}

@media screen{
:root{--ink:#203b38;--muted:#5f726f;--quiet:#697c79;--canvas:#f1f5f4;--line:#dbe5e2;--soft:#f5f9f8;--blue:#087f73;--blue-deep:#075d55;--teal:#179b8c;
--ui-xs:12px;--ui-sm:13px;--ui-body:14px;--ui-title:26px;--ui-subtitle:18px;--ui-1:4px;--ui-2:8px;--ui-3:12px;--ui-4:16px;--ui-6:24px;--ui-8:32px;--ui-12:48px;--ui-16:64px;--ui-radius:6px;--ui-radius-lg:12px;--ui-page:1120px}
body{font-size:var(--ui-body);line-height:1.8}
.report{width:calc(100% - 48px);max-width:var(--ui-page);margin:var(--ui-6) auto;box-shadow:0 4px 32px rgba(32,59,56,.06);border:1px solid var(--line);border-radius:var(--ui-radius-lg)}
.docnav{gap:var(--ui-1);padding:var(--ui-3) var(--ui-6);background:var(--paper);border-bottom:1px solid var(--line);overflow-x:auto;white-space:nowrap;flex-wrap:nowrap;border-radius:var(--ui-radius-lg) var(--ui-radius-lg) 0 0}
.docnav a{padding:var(--ui-2) var(--ui-3);border-radius:var(--ui-radius);font-size:var(--ui-xs);color:var(--muted);flex-shrink:0;border-bottom:0}
.docnav a em{font-size:var(--ui-xs);color:var(--quiet)}
.docnav a:hover,.docnav a.is-current{background:var(--soft);color:var(--blue-deep)}.docnav a.is-current em{color:var(--blue)}
.docnav .docnav-print{padding:var(--ui-2) var(--ui-4);font-size:var(--ui-xs);border:0;background:var(--blue-deep);color:var(--paper);border-radius:var(--ui-radius);flex-shrink:0;min-height:40px;cursor:pointer}
a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--blue);outline-offset:var(--ui-1)}
.cover{padding:32px var(--ui-12) var(--ui-8)}.cover-logo{margin:0 0 20px}.cover-logo img{height:40px}
.brandline{font-size:var(--ui-xs);letter-spacing:.14em}
.cover h1{font-size:40px;line-height:1.3;color:var(--ink);letter-spacing:-.03em;margin:12px 0 var(--ui-3);text-wrap:balance}
.subtitle{font-size:var(--ui-body);color:var(--muted)}
.b-intro{margin-top:var(--ui-6);padding:24px 28px;border-radius:0 8px 8px 0}
.b-eyebrow{font-size:var(--ui-xs)}.b-intro h2{font-size:22px;line-height:1.6;margin:8px 0 12px}.b-intro p{font-size:var(--ui-sm);line-height:1.8}
.meta{margin:24px 0 0;grid-template-columns:1.4fr 1.5fr .8fr .8fr;border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:var(--ui-4) 0;gap:var(--ui-4)}
.meta-item{padding:0;border:0;min-width:0}.meta-item span{font-size:var(--ui-xs);color:var(--quiet);margin-bottom:var(--ui-1);text-transform:none;letter-spacing:.02em}
.meta-item strong{font-size:var(--ui-xs);color:var(--ink);font-weight:600}
.twist-metrics{margin:0 var(--ui-12) var(--ui-3);border-bottom:1px solid var(--line)}
.twist-metric{padding:var(--ui-6) var(--ui-4);border-right:1px solid var(--line);border-top:3px solid var(--blue)}
.twist-metric:first-child{padding-left:0}
.twist-metric.tm-watch{background:var(--amber-soft);padding-left:var(--ui-4)}
.twist-metric b{font-size:36px;letter-spacing:-.04em;line-height:1.2;font-weight:600}
.twist-metric span{font-size:var(--ui-xs);color:var(--ink);margin-top:var(--ui-2)}
.twist-metric i{font-size:var(--ui-xs);color:var(--muted);line-height:1.6;margin-top:var(--ui-2)}
.content{padding:0 var(--ui-12) var(--ui-16)}
.section{padding-top:var(--ui-12);scroll-margin-top:80px;min-width:0}.section+.section{margin-top:var(--ui-12);border-top:1px solid var(--line)}
.section-head{gap:var(--ui-4);margin-bottom:var(--ui-6);align-items:center}
.section-no{display:flex;align-items:center;justify-content:center;width:40px;height:40px;background:var(--soft);border-radius:var(--ui-radius);font-size:var(--ui-body);padding:0}
.section-title h2{font-size:var(--ui-title);line-height:1.4;color:var(--ink);text-wrap:balance}
.section-title p{font-size:var(--ui-xs);color:var(--quiet);letter-spacing:.1em;margin-top:var(--ui-1)}
p,.lead,.conclusion p,.watchbox p,.hits-card p,.chart-copy-text p,ul,ol{font-size:var(--ui-body);line-height:1.8;color:var(--muted);text-wrap:pretty}
.lead{margin:0 0 var(--ui-6)}
.conclusion,.watchbox{padding:var(--ui-6);border-radius:var(--ui-radius);margin-bottom:var(--ui-4)}
.conclusion{background:var(--soft)}.conclusion-label{font-size:var(--ui-xs)}
.conclusion h3{font-size:20px;line-height:1.6;color:var(--ink);margin:var(--ui-2) 0 var(--ui-3);text-wrap:balance}
.watchbox{border:1px solid var(--line)}.watchbox h3{font-size:var(--ui-body);margin-bottom:var(--ui-2)}
.panel{margin-top:var(--ui-8)}
.panel h3,.chart-copy h3{font-size:var(--ui-subtitle);line-height:1.6;color:var(--ink);padding-bottom:var(--ui-3);border-bottom:1px solid var(--line);margin-bottom:var(--ui-4)}
.panel h4{font-size:var(--ui-body);line-height:1.6;margin:var(--ui-6) 0 var(--ui-3)}
.table-wrap{border:1px solid var(--line);border-radius:var(--ui-radius);margin-top:var(--ui-4)}
th{background:var(--soft);font-size:var(--ui-xs);color:var(--muted);border-top:0;border-bottom:1px solid var(--line);padding:var(--ui-3) var(--ui-4)}
td{font-size:var(--ui-sm);padding:var(--ui-3) var(--ui-4);border-bottom:1px solid var(--line);line-height:1.65}
tbody tr:last-child td{border-bottom:0}tbody tr:hover td{background:#f7faf9}
.table-caption{font-size:var(--ui-xs);color:var(--muted);margin-top:var(--ui-2)}
.badge{font-size:var(--ui-xs);padding:var(--ui-1) var(--ui-2);border-radius:var(--ui-radius);white-space:nowrap}
.note,.spec{font-size:var(--ui-sm);line-height:1.8;padding:var(--ui-4);margin-top:var(--ui-4)}
.steps{border-color:var(--line);margin-top:var(--ui-6)}.step{padding:var(--ui-4);border-color:var(--line)}
.step small{font-size:var(--ui-xs)}.step b{font-size:var(--ui-sm);color:var(--blue-deep);margin:var(--ui-2) 0}.step span{font-size:var(--ui-sm)}
.specs{grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--ui-3)}.spec{margin-top:0}
.grid-2{gap:var(--ui-6)}
.hits-grid{gap:var(--ui-4)}.hits-card{padding:var(--ui-4);background:var(--soft);border-radius:var(--ui-radius)}
.hits-card h4{margin-top:0;border-color:var(--line);font-size:var(--ui-sm)}
.b-bars{gap:32px;margin:24px 0 32px}.b-bar-title{font-size:var(--ui-sm);margin-bottom:12px}.b-track{border-radius:3px}.b-legend{font-size:var(--ui-xs);margin-top:10px}.b-legend i{border-radius:2px}
.chart-card{margin-top:var(--ui-12);border:1px solid var(--line);border-radius:var(--ui-radius-lg);overflow:hidden}
.chart-copy{padding:var(--ui-6);background:var(--soft);border-bottom:1px solid var(--line)}
.chart-copy-text{gap:var(--ui-6)}.chart-copy-text p:first-child{padding-right:var(--ui-6);border-color:var(--line)}
.chart-copy-text p strong{font-size:var(--ui-xs);margin-bottom:var(--ui-2);letter-spacing:.04em}
.chart-image{padding:var(--ui-6)}.chart-image img{max-height:none}
.chart-caption{font-size:var(--ui-xs);padding:var(--ui-4) var(--ui-6) var(--ui-6);color:var(--muted)}
.placeholder-box{font-size:var(--ui-sm);padding:var(--ui-8);margin:var(--ui-6);border-radius:var(--ui-radius)}
.dictionary td:first-child{width:32%;overflow-wrap:anywhere;font-size:var(--ui-xs)}
details{border-radius:var(--ui-radius);padding:0 var(--ui-6);margin-top:var(--ui-4)}
details summary{font-size:var(--ui-body);padding:var(--ui-4) 0}
.footer{padding:var(--ui-6) var(--ui-12);border-color:var(--line);font-size:var(--ui-xs);gap:var(--ui-6);color:var(--muted)}
@media(max-width:960px){
.report{width:100%;margin:0;border:0;border-radius:0}.docnav{padding:var(--ui-2) var(--ui-4);border-radius:0}
.cover{padding:var(--ui-8)}.meta{grid-template-columns:1fr 1fr}.content{padding:0 var(--ui-8) var(--ui-12)}
.twist-metrics{margin:0 var(--ui-8);grid-template-columns:repeat(2,minmax(0,1fr))}
.twist-metric:nth-child(3){padding-left:0}.twist-metric:nth-child(2){border-right:0}.twist-metric:nth-child(-n+2){border-bottom:1px solid var(--line)}
.grid-2,.hits-grid,.hits-grid.hits-grid-4,.hits-grid.hits-grid-2,.b-bars{grid-template-columns:1fr}
.steps{grid-template-columns:1fr 1fr}.footer{padding:var(--ui-6) var(--ui-8)}
.chart-copy-text{grid-template-columns:1fr}
.chart-copy-text p:first-child{padding-right:0;border-right:0;padding-bottom:var(--ui-4);border-bottom:1px solid var(--line)}}
@media(max-width:680px){
.cover{padding:var(--ui-8) 20px var(--ui-6)}.cover h1{font-size:30px}.brandline{font-size:10px;letter-spacing:.08em}
.content{padding:0 20px var(--ui-12)}.twist-metrics{margin:0 20px}.twist-metric{padding:var(--ui-4) var(--ui-3)}.twist-metric b{font-size:30px}
.section-title h2{font-size:22px}.section-head{gap:var(--ui-3)}.section-no{width:32px;height:32px}
.conclusion,.watchbox{padding:var(--ui-4)}.table-wrap table{min-width:520px}.dictionary{min-width:0;table-layout:fixed}.dictionary td:first-child{width:42%}
.steps,.specs{grid-template-columns:1fr}.step{border-right:0;border-bottom:1px solid var(--line)}.step:last-child{border-bottom:0}
.chart-copy,.chart-image{padding:var(--ui-4)}.chart-caption{padding:var(--ui-4)}
.footer{flex-direction:column;padding:var(--ui-6) 20px}.footer .right{text-align:left}}
}
@media screen and (prefers-reduced-motion:reduce){html{scroll-behavior:auto}*,*::before,*::after{transition:none!important}}
@media print{
@page{size:A4;margin:0}body{background:#fff;font-size:9pt}
.report{width:210mm;margin:0;box-shadow:none;border:0}.docnav,.no-print{display:none!important}
.cover{padding:12mm 18mm 8mm}.cover h1{margin:6mm 0 3mm}.meta{margin-top:6mm}
.section{break-before:auto}.section-head,h2,h3{break-after:avoid}
.chart-card{break-inside:avoid}.chart-image img{max-height:120mm}
.grid-2,.hits-card,.conclusion,.watchbox{break-inside:avoid}
details{border:0;padding:0}details summary{list-style:none}details:not([open])>*:not(summary){display:block}
.content{padding-bottom:4mm}.footer{break-before:avoid}
a{color:inherit;text-decoration:none}
*{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}}
"""

JS = r"""
(function(){
  var links=document.querySelectorAll('.docnav a[href^="#"]');
  var map={};links.forEach(function(a){map[a.getAttribute('href').slice(1)]=a;});
  if('IntersectionObserver' in window){
    var obs=new IntersectionObserver(function(entries){
      entries.forEach(function(e){if(e.isIntersecting){links.forEach(function(a){a.classList.remove('is-current');});
        var a=map[e.target.id];if(a)a.classList.add('is-current');}});
    },{rootMargin:'-18% 0px -70% 0px'});
    Object.keys(map).forEach(function(id){var el=document.getElementById(id);if(el)obs.observe(el);});
  }
  window.addEventListener('beforeprint',function(){document.querySelectorAll('details').forEach(function(d){d.setAttribute('data-was-open',d.open?'1':'0');d.open=true;});});
  window.addEventListener('afterprint',function(){document.querySelectorAll('details').forEach(function(d){d.open=d.getAttribute('data-was-open')==='1';});});
})();
function tcImgError(img){img.hidden=true;var box=img.nextElementSibling;if(box){box.style.display='block';}}
"""


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def text(block_text, allow_html=False) -> str:
    """段落文本：默认转义；allow_html 时透传（内容层自行负责）。支持 **加粗**。"""
    if block_text is None:
        return ""
    if allow_html:
        return str(block_text)
    s = esc(block_text)
    # 轻量 markdown：**bold**
    parts = s.split("**")
    if len(parts) > 1 and len(parts) % 2 == 1:
        out = []
        for i, p in enumerate(parts):
            out.append(f"<strong>{p}</strong>" if i % 2 else p)
        s = "".join(out)
    return s


class Renderer:
    def __init__(self, spec: dict, logo_markup: str | None):
        self.spec = spec
        self.logo = logo_markup
        self.section_no = 0
        self.chart_no = 0
        self.table_no = 0

    # ---------- blocks ----------
    def blocks(self, blocks: list) -> str:
        return "".join(self.block(b) for b in blocks or [])

    def block(self, b: dict) -> str:
        t = b.get("type", "p")
        fn = getattr(self, f"b_{t}", None)
        if fn is None:
            raise SystemExit(f"未知块类型: {t}")
        return fn(b)

    def b_p(self, b):
        return f'<p>{text(b.get("text"), b.get("html", False))}</p>'

    def b_lead(self, b):
        return f'<p class="lead">{text(b.get("text"), b.get("html", False))}</p>'

    def b_list(self, b):
        tag = "ol" if b.get("ordered") else "ul"
        items = "".join(f"<li>{text(i, b.get('html', False))}</li>" for i in b.get("items", []))
        return f"<{tag}>{items}</{tag}>"

    def b_conclusion(self, b):
        return (
            '<div class="conclusion"><div class="conclusion-label">'
            f'{esc(b.get("label", "结论"))}</div><h3>{esc(b.get("title"))}</h3>'
            f'<p>{text(b.get("text"), b.get("html", False))}</p></div>'
        )

    def b_watchbox(self, b):
        return (
            f'<div class="watchbox"><h3>{esc(b.get("title", "使用时注意"))}</h3>'
            f'<p>{text(b.get("text"), b.get("html", False))}</p></div>'
        )

    def b_note(self, b):
        cls = "note warning" if b.get("warning") else "note"
        return f'<div class="{cls}">{text(b.get("text"), b.get("html", False))}</div>'

    def b_panel(self, b):
        h = f'<h3>{esc(b["title"])}</h3>' if b.get("title") else ""
        return f'<div class="panel">{h}{self.blocks(b.get("blocks"))}</div>'

    def b_h4(self, b):
        return f'<h4>{esc(b.get("text"))}</h4>'

    def b_table(self, b):
        cols = b["columns"]
        ths = "".join(
            f'<th{" class=\"number\"" if c.get("number") else ""}>{esc(c["header"])}</th>' for c in cols
        )
        trs = []
        for row in b.get("rows", []):
            tds = []
            for c in cols:
                v = row.get(c["key"], "—") if isinstance(row, dict) else row[cols.index(c)]
                if v is None or v == "":
                    v = "—"
                cls = ' class="number"' if c.get("number") else ""
                tds.append(f"<td{cls}>{text(v, c.get('html', False))}</td>")
            trs.append(f"<tr>{''.join(tds)}</tr>")
        cls = "table-wrap dictionary" if b.get("dictionary") else "table-wrap"
        cap = ""
        if b.get("caption"):
            self.table_no += 1
            cap = f'<p class="table-caption"><strong>表 {self.section_no}.{self.table_no}</strong>　{text(b["caption"])}</p>'
        return f'<div class="{cls}"><table><thead><tr>{ths}</tr></thead><tbody>{"".join(trs)}</tbody></table></div>{cap}'

    def b_kv(self, b):
        items = "".join(
            f'<div class="spec"><b>{esc(i["label"])}</b>　{text(i.get("value"), i.get("html", False))}</div>'
            for i in b.get("items", [])
        )
        return f'<div class="specs">{items}</div>'

    def b_steps(self, b):
        items = b.get("items", [])
        cols = len(items) or 1
        steps = "".join(
            f'<div class="step"><small>{i + 1:02d}</small><b>{esc(s["title"])}</b><span>{text(s.get("text"))}</span></div>'
            for i, s in enumerate(items)
        )
        return f'<div class="steps" style="grid-template-columns:repeat({cols},1fr)">{steps}</div>'

    def b_cards(self, b):
        items = b.get("items", [])
        n = len(items)
        cls = "hits-grid" + (" hits-grid-4" if n == 4 else " hits-grid-2" if n == 2 else "")
        cards = "".join(
            f'<div class="hits-card {esc(i.get("tone", "neutral"))}"><h4>{esc(i["title"])}</h4><p>{text(i.get("text"), i.get("html", False))}</p></div>'
            for i in items
        )
        return f'<div class="{cls}">{cards}</div>'

    def b_bars(self, b):
        out = []
        for bar in b.get("items", []):
            segs, legend, aria = [], [], []
            for s in bar.get("segments", []):
                pct = float(s.get("pct", 0))
                color = s.get("color", "gray")
                segs.append(f'<span class="c-{esc(color)}" style="width:{pct:.2f}%"></span>')
                legend.append(f'<span><i class="c-{esc(color)}"></i>{esc(s["label"])} {pct:.1f}%</span>')
                aria.append(f'{s["label"]} {pct:.1f}%')
            total = f'<span>{esc(bar["total"])}</span>' if bar.get("total") else ""
            out.append(
                f'<div><div class="b-bar-title"><strong>{esc(bar["title"])}</strong>{total}</div>'
                f'<div class="b-track" role="img" aria-label="{esc("；".join(aria))}">{"".join(segs)}</div>'
                f'<div class="b-legend">{"".join(legend)}</div></div>'
            )
        return f'<div class="b-bars">{"".join(out)}</div>'

    def b_chart(self, b):
        self.chart_no += 1
        no = f"{self.section_no}.{self.chart_no}"
        src = b["image"]
        alt = b.get("alt") or b.get("title", "")
        copy = ""
        if b.get("principle") or b.get("interpretation"):
            copy = (
                '<div class="chart-copy-text">'
                f'<p><strong>{esc(b.get("principle_label", "原理说明"))}</strong>{text(b.get("principle"), b.get("html", False))}</p>'
                f'<p><strong>{esc(b.get("interpretation_label", "结果解读"))}</strong>{text(b.get("interpretation"), b.get("html", False))}</p>'
                "</div>"
            )
        cap = text(b.get("caption"), b.get("html", False))
        return (
            '<article class="chart-card">'
            f'<div class="chart-copy"><h3>{no}　{esc(b["title"])}</h3>{copy}</div>'
            f'<div class="chart-image"><img src="{esc(src)}" alt="{esc(alt)}" onerror="tcImgError(this)">'
            f'<div class="placeholder-box">未找到图片文件 <code>{esc(src)}</code>。请确认报告与 plot 目录放在同一文件夹中。</div></div>'
            f'<div class="chart-caption"><strong>图 {no} · {esc(b["title"])}</strong>　{cap}</div>'
            "</article>"
        )

    def b_grid2(self, b):
        return f'<div class="grid-2"><div>{self.blocks(b.get("left"))}</div><div>{self.blocks(b.get("right"))}</div></div>'

    def b_collapse(self, b):
        op = " open" if b.get("open") else ""
        return f'<details{op}><summary>{esc(b["summary"])}</summary>{self.blocks(b.get("blocks"))}</details>'

    def b_html(self, b):
        return b.get("html", "")

    def b_files(self, b):
        cols = [
            {"header": "文件", "key": "name"},
            {"header": "内容", "key": "desc"},
        ]
        if any("size" in i for i in b.get("items", [])):
            cols.append({"header": "大小", "key": "size", "number": True})
        if any("sha256" in i for i in b.get("items", [])):
            cols.append({"header": "SHA256（前 16 位）", "key": "sha256"})
        rows = []
        for i in b.get("items", []):
            r = dict(i)
            if "sha256" in r and r["sha256"]:
                r["sha256"] = r["sha256"][:16]
            rows.append(r)
        return self.b_table({"columns": cols, "rows": rows, "caption": b.get("caption")})

    # ---------- page ----------
    def section(self, s: dict, no_label: str | None = None) -> str:
        self.section_no += 1
        self.chart_no = 0
        self.table_no = 0
        label = no_label or f"{self.section_no:02d}"
        kicker = f'<p>{esc(s["kicker"])}</p>' if s.get("kicker") else ""
        return (
            f'<section id="{esc(s["id"])}" class="section">'
            f'<div class="section-head"><div class="section-no">{esc(label)}</div>'
            f'<div class="section-title"><h2>{esc(s["title"])}</h2>{kicker}</div></div>'
            f'{self.blocks(s.get("blocks"))}</section>'
        )

    def render(self) -> str:
        sp = self.spec
        sections = sp.get("sections", [])
        appendix = sp.get("appendix")
        nav_items = []
        for i, s in enumerate(sections, 1):
            nav_items.append(f'<a href="#{esc(s["id"])}"><em>{i:02d}</em>{esc(s.get("short", s["title"]))}</a>')
        if appendix:
            aid = appendix.get("id", "sec-appendix")
            nav_items.append(f'<a href="#{esc(aid)}"><em>{len(sections) + 1:02d}</em>{esc(appendix.get("short", appendix.get("title", "附录")))}</a>')
        nav = (
            '<nav class="docnav no-print" aria-label="章节导航">'
            + "".join(nav_items)
            + '<button class="docnav-print" type="button" onclick="window.print()">打印 / 存为 PDF</button></nav>'
        )

        logo_cover = f'<div class="cover-logo">{self.logo}</div>' if self.logo else ""
        intro = ""
        if sp.get("intro"):
            it = sp["intro"]
            intro = (
                f'<div class="b-intro"><div class="b-eyebrow">{esc(it.get("eyebrow", "主要结论"))}</div>'
                f'<h2>{esc(it.get("title"))}</h2><p>{text(it.get("text"), it.get("html", False))}</p></div>'
            )
        meta = ""
        if sp.get("meta"):
            items = "".join(
                f'<div class="meta-item"><span>{esc(m["label"])}</span><strong>{esc(m["value"])}</strong></div>'
                for m in sp["meta"]
            )
            n = len(sp["meta"])
            meta = f'<div class="meta" style="grid-template-columns:repeat({n},1fr)">{items}</div>'
        cover = (
            f'<header class="cover">{logo_cover}<div class="brandline">{esc(sp.get("genre", "结果交付报告"))}</div>'
            f'<h1>{esc(sp["title"])}</h1><p class="subtitle">{esc(sp.get("subtitle", ""))}</p>{intro}{meta}</header>'
        )
        kpis = ""
        if sp.get("kpis"):
            cells = []
            for k in sp["kpis"]:
                cls = "twist-metric tm-watch" if k.get("watch") else "twist-metric"
                cells.append(
                    f'<div class="{cls}"><b>{esc(k.get("value", "—"))}</b><span>{esc(k["label"])}</span><i>{esc(k.get("note", ""))}</i></div>'
                )
            n = len(sp["kpis"])
            kpis = f'<div class="twist-metrics" style="grid-template-columns:repeat({n},1fr)">{"".join(cells)}</div>'

        body_sections = "".join(self.section(s) for s in sections)
        app_html = ""
        if appendix:
            appendix = dict(appendix)
            appendix.setdefault("id", "sec-appendix")
            appendix.setdefault("title", "附录")
            app_html = self.section(appendix)

        ft = sp.get("footer", {})
        logo_foot = f'<span class="footer-logo">{self.logo}</span>' if self.logo else ""
        footer = (
            f'<footer class="footer"><div class="foot-brand">{logo_foot}<div><strong>{esc(ft.get("left", sp["title"]))}</strong>'
            f'<br>{esc(ft.get("left_sub", ""))}</div></div><div class="right">{text(ft.get("right", ""), ft.get("html", False))}</div></footer>'
        )
        lang = sp.get("lang", "zh-CN")
        return (
            f'<!DOCTYPE html><html lang="{esc(lang)}"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            f'<title>{esc(sp["title"])}</title><style>{CSS}</style></head><body>'
            f'<main class="report">{nav}{cover}{kpis}<div class="content">{body_sections}{app_html}</div>{footer}</main>'
            f"<script>{JS}</script></body></html>"
        )


def load_logo(path: Path | None) -> str | None:
    if not path or not path.is_file():
        return None
    if path.suffix.lower() == ".svg":
        return path.read_text(encoding="utf-8").strip() or None
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(path.suffix.lower())
    if not mime:
        return None
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'<img src="data:{mime};base64,{data}" alt="logo">'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("content", type=Path, help="内容规范 JSON")
    ap.add_argument("-o", "--output", type=Path, required=True, help="输出 HTML 路径")
    ap.add_argument("--logo", type=Path, default=None, help="品牌 logo（svg/png），内联进 HTML")
    args = ap.parse_args()

    spec = json.loads(args.content.read_text(encoding="utf-8"))
    logo = load_logo(args.logo) if args.logo else None
    out = Renderer(spec, logo).render()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(out, encoding="utf-8")
    # 检查图片引用是否存在（相对于输出目录）
    missing = []
    for s in spec.get("sections", []) + ([spec["appendix"]] if spec.get("appendix") else []):
        stack = list(s.get("blocks", []))
        while stack:
            b = stack.pop()
            if b.get("type") == "chart":
                p = args.output.parent / b["image"]
                if not p.is_file():
                    missing.append(b["image"])
            for key in ("blocks", "left", "right"):
                stack.extend(b.get(key, []) or [])
    print(f"已写入 {args.output}（{args.output.stat().st_size / 1024:.0f} KB）")
    if missing:
        print("缺失图片：", ", ".join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
