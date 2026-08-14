"""Generate a visual store dashboard with per-day data and date picker."""

import json
import re
import sys
import os
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MONITOR_DIR = Path(os.environ.get("MONITOR_DIR", r"H:\claude-work\监控达人视频+直播"))
sys.path.insert(0, str(MONITOR_DIR))

import shop_report

MX_TZ = timezone(timedelta(hours=-6))
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "lanomiz2026")

STATUS_ZH = {
    "UNPAID": "待付款", "ON_HOLD": "留置中", "AWAITING_SHIPMENT": "待发货",
    "PARTIALLY_SHIPPING": "部分发货", "AWAITING_COLLECTION": "待揽收",
    "IN_TRANSIT": "运输中", "DELIVERED": "已送达", "COMPLETED": "已完成",
    "CANCELLED": "已取消",
}
SAMPLE_STATUS_ZH = {
    "PENDING": "待审核", "SHIPPED": "已发货", "COMPLETED": "已完成",
    "CONTENT_PENDING": "待发布", "AWAITING_SHIPMENT": "待发货",
    "OVERDUE_CANCELLED": "超时取消", "WITHDRAW_CANCELLED": "撤回取消",
    "REJECT_CANCELLED": "拒绝取消",
}
MX_STATE_RANGES = [
    (0,16,"墨西哥城"),(20,20,"阿瓜斯"),(21,22,"下加州"),(23,23,"南下加州"),
    (24,24,"坎佩切"),(25,27,"科阿韦拉"),(28,28,"科利马"),(29,30,"恰帕斯"),
    (31,33,"奇瓦瓦"),(34,35,"杜兰戈"),(36,38,"瓜纳华托"),(39,41,"格雷罗"),
    (42,43,"伊达尔戈"),(44,49,"哈利斯科"),(50,57,"墨西哥州"),(58,61,"米却肯"),
    (62,62,"莫雷洛斯"),(63,63,"纳亚里特"),(64,67,"新莱昂"),(68,71,"瓦哈卡"),
    (72,75,"普埃布拉"),(76,76,"克雷塔罗"),(77,77,"金塔纳罗奥"),(78,79,"圣路易斯"),
    (80,82,"锡那罗亚"),(83,85,"索诺拉"),(86,86,"塔巴斯科"),(87,89,"塔毛利帕斯"),
    (90,90,"特拉斯卡拉"),(91,96,"韦拉克鲁斯"),(97,97,"尤卡坦"),(98,99,"萨卡特卡斯"),
]

def state_from_zip(pc):
    d = (pc or "").strip()[:2]
    if not d.isdigit(): return None
    p = int(d)
    for lo, hi, name in MX_STATE_RANGES:
        if lo <= p <= hi: return name
    return None


def fetch_all_samples(keys, cipher):
    all_apps, pt, pg = [], "", 0
    while True:
        pg += 1
        params = {"shop_cipher": cipher, "page_size": "50"}
        if pt: params["page_token"] = pt
        data = shop_report.api_call(keys, "POST",
            "/affiliate_seller/202508/sample_applications/search", params, {})
        apps = data.get("sample_applications") or []
        all_apps.extend(apps)
        sys.stderr.write(f"\r  Samples: {len(all_apps)}")
        pt = data.get("next_page_token") or ""
        if not pt: break
        time.sleep(1)
    sys.stderr.write("\n")
    return all_apps


def analyze_orders(orders):
    """Analyze a list of orders into chart-ready data."""
    valid = [o for o in orders if o.get("status") != "CANCELLED"]
    cancelled = [o for o in orders if o.get("status") == "CANCELLED"]

    status_count = defaultdict(int)
    payment_count = defaultdict(int)
    product_qty = defaultdict(int)
    size_qty = defaultdict(int)
    color_qty = defaultdict(int)
    state_count = defaultdict(int)
    provider_count = defaultdict(int)
    hour_count = defaultdict(int)
    cod_count = 0
    total_revenue = 0
    total_items = 0
    sample_count = 0

    for o in orders:
        status_count[STATUS_ZH.get(o.get("status", ""), o.get("status", ""))] += 1

    for o in valid:
        total_revenue += float((o.get("payment") or {}).get("total_amount") or 0)
        items = o.get("line_items") or []
        total_items += len(items)
        if o.get("is_cod"): cod_count += 1
        if o.get("is_sample_order"): sample_count += 1
        payment_count[o.get("payment_method_name") or "未知"] += 1
        provider_count[o.get("shipping_provider") or "未分配"] += 1
        s = state_from_zip((o.get("recipient_address") or {}).get("postal_code") or "")
        if s: state_count[s] += 1
        try:
            h = datetime.fromtimestamp(int(o.get("create_time", 0)), MX_TZ).hour
            hour_count[h] += 1
        except: pass
        for item in items:
            name = (item.get("product_name") or "未知")
            if len(name) > 35: name = name[:35] + "…"
            product_qty[name] += 1
            sku = (item.get("sku_name") or "").strip()
            if "," in sku:
                color, size = (p.strip() for p in sku.rsplit(",", 1))
                if color:
                    color = re.sub(r"\s*\([^)]*\)", "", color).strip() or color
                    color_qty[color] += 1
                if size: size_qty[size.upper()] += 1
            elif sku: size_qty[sku.upper()] += 1

    return {
        "total": len(orders), "valid": len(valid), "cancelled": len(cancelled),
        "revenue": round(total_revenue, 2),
        "items": total_items, "sample_orders": sample_count,
        "avg_order": round(total_revenue / len(valid), 2) if valid else 0,
        "cod_rate": round(cod_count / len(valid) * 100, 1) if valid else 0,
        "status": dict(status_count),
        "payment": dict(payment_count),
        "products": sorted(product_qty.items(), key=lambda x: -x[1])[:8],
        "sizes": dict(size_qty),
        "colors": sorted(color_qty.items(), key=lambda x: -x[1])[:10],
        "regions": sorted(state_count.items(), key=lambda x: -x[1])[:10],
        "providers": sorted(provider_count.items(), key=lambda x: -x[1]),
        "hours": {str(k): v for k, v in sorted(hour_count.items())},
    }


def collect_data(keys, cipher):
    now = datetime.now(MX_TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Fetch 14 days of orders
    start = today_start - timedelta(days=14)
    sys.stderr.write("Fetching orders (14 days)...\n")
    all_orders = shop_report.fetch_orders(keys, cipher, int(start.timestamp()), int(now.timestamp()))
    sys.stderr.write(f"  {len(all_orders)} orders\n")

    # Group by MX date
    by_day = defaultdict(list)
    for o in all_orders:
        try:
            d = datetime.fromtimestamp(int(o.get("create_time", 0)), MX_TZ).strftime("%Y-%m-%d")
            by_day[d].append(o)
        except: pass

    # Build per-day analysis
    days_data = {}
    trend = []
    for i in range(14):
        d = (today_start - timedelta(days=13 - i)).strftime("%Y-%m-%d")
        day_orders = by_day.get(d, [])
        analysis = analyze_orders(day_orders)
        days_data[d] = analysis
        trend.append({"d": d[5:], "orders": analysis["total"], "revenue": analysis["revenue"]})

    # Samples
    sys.stderr.write("Fetching samples...\n")
    samples = fetch_all_samples(keys, cipher)

    sample_status = defaultdict(int)
    partner_data = defaultdict(lambda: {"apps": 0, "creators": set()})
    for s in samples:
        sample_status[SAMPLE_STATUS_ZH.get(s.get("status", ""), s.get("status", ""))] += 1
        pn = s.get("partner_name") or ""
        if pn:
            partner_data[pn]["apps"] += 1
            u = (s.get("creator") or {}).get("username", "")
            if u: partner_data[pn]["creators"].add(u)

    partners = [{"name": k, "apps": v["apps"], "creators": len(v["creators"])}
                for k, v in sorted(partner_data.items(), key=lambda x: -x[1]["apps"])]

    # Yesterday as default
    yesterday = (today_start - timedelta(days=1)).strftime("%Y-%m-%d")

    return {
        "days": days_data,
        "trend": trend,
        "default_day": yesterday,
        "available_days": sorted(days_data.keys(), reverse=True),
        "samples": {
            "total": len(samples),
            "status": dict(sample_status),
            "partners": partners,
        },
    }


def build_html(data, updated_at):
    import hashlib
    pw_hash = hashlib.sha256(DASHBOARD_PASSWORD.encode()).hexdigest()
    d = json.dumps(data, ensure_ascii=False)
    return TEMPLATE.replace("__DATA__", d).replace("__UPDATED__", updated_at).replace("__PASSWORD_HASH__", pw_hash)


TEMPLATE = r'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>LANOMIZ 运营看板</title>
<style>
:root{
  --bg:#FAF8F6;--card:#FFF;--bdr:#EDE8E3;
  --t1:#1C1A18;--t2:#6B6560;--t3:#9E9890;
  --ac:#C4536A;--acl:rgba(196,83,106,.08);
  --c1:#2a78d6;--c2:#eb6834;--c3:#1baf7a;--c4:#eda100;--c5:#e87ba4;--c6:#4a3aa7;--c7:#e34948;--c8:#008300;
  --good:#1baf7a;--warn:#eda100;--bad:#e34948;--div:#E8E3DE;
}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#1C1A18;--card:#282422;--bdr:#3A3632;
  --t1:#F5F0EB;--t2:#B0A99F;--t3:#7A746E;
  --ac:#E0758B;--acl:rgba(224,117,139,.12);
  --c1:#3987e5;--c2:#d95926;--c3:#199e70;--c4:#c98500;--c5:#d55181;--c6:#9085e9;--c7:#e66767;--c8:#008300;
  --div:#3A3632;
}}
:root[data-theme="dark"]{
  --bg:#1C1A18;--card:#282422;--bdr:#3A3632;
  --t1:#F5F0EB;--t2:#B0A99F;--t3:#7A746E;
  --ac:#E0758B;--acl:rgba(224,117,139,.12);
  --c1:#3987e5;--c2:#d95926;--c3:#199e70;--c4:#c98500;--c5:#d55181;--c6:#9085e9;--c7:#e66767;--c8:#008300;
  --div:#3A3632;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--t1);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums;line-height:1.5;-webkit-font-smoothing:antialiased;-webkit-text-size-adjust:100%}

#login{display:flex;align-items:center;justify-content:center;min-height:100dvh;padding:24px}
.lbox{background:var(--card);border:1px solid var(--bdr);border-radius:16px;padding:40px 32px;width:100%;max-width:320px;text-align:center}
.lbox h2{font-size:18px;font-weight:700;margin-bottom:4px}
.lbox .sub{font-size:13px;color:var(--t2);margin-bottom:20px}
.lbox input{width:100%;padding:12px 16px;border:1px solid var(--bdr);border-radius:8px;font-size:15px;background:var(--bg);color:var(--t1);outline:none;-webkit-appearance:none}
.lbox input:focus{border-color:var(--ac)}
.lbox button{width:100%;margin-top:12px;padding:12px;border:none;border-radius:8px;background:var(--ac);color:#fff;font-size:15px;font-weight:600;cursor:pointer}
.lbox .err{color:var(--bad);font-size:13px;margin-top:8px;display:none}

#dash{display:none}
.page{max-width:1100px;margin:0 auto;padding:24px 14px 60px}
.hdr{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.hdr h1{font-size:20px;font-weight:700}
.date-pick{display:flex;align-items:center;gap:8px}
.date-pick select{padding:8px 12px;border:1px solid var(--bdr);border-radius:8px;font-size:14px;font-weight:600;background:var(--card);color:var(--t1);cursor:pointer;outline:none}
.date-pick .upd{font-size:11px;color:var(--t3)}

.kpi-row{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:16px 0}
.kpi{background:var(--card);border:1px solid var(--bdr);border-radius:10px;padding:12px 14px}
.kpi .l{font-size:10px;color:var(--t3);text-transform:uppercase;letter-spacing:.04em}
.kpi .v{font-size:22px;font-weight:700;margin-top:2px;letter-spacing:-.02em}
.kpi .d{font-size:10px;color:var(--t2);margin-top:1px}
.kpi .up{color:var(--good)}
.kpi .dn{color:var(--bad)}

.sec{margin-top:22px}
.sec-t{font-size:13px;font-weight:600;color:var(--t2);margin-bottom:8px;padding-bottom:5px;border-bottom:1px solid var(--div)}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.cc{background:var(--card);border:1px solid var(--bdr);border-radius:10px;padding:14px}
.cc h3{font-size:12px;font-weight:600;margin-bottom:8px}
.cc canvas{width:100%!important;height:200px!important}
.cc.full{grid-column:span 2}

.leg{display:flex;flex-wrap:wrap;gap:4px 12px;margin-top:6px}
.li{display:flex;align-items:center;gap:4px;font-size:11px;color:var(--t2)}
.ld{width:8px;height:8px;border-radius:50%;flex-shrink:0}

.hb{margin:3px 0}
.hb-l{font-size:11px;color:var(--t2);margin-bottom:1px;display:flex;justify-content:space-between}
.hb-t{height:16px;background:var(--div);border-radius:3px;overflow:hidden}
.hb-f{height:100%;border-radius:3px;min-width:2px}

.pl{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}
.pc{background:var(--acl);border-radius:8px;padding:10px 12px}
.pc .nm{font-size:13px;font-weight:600}
.pc .inf{font-size:11px;color:var(--t2);margin-top:3px}

@media(max-width:640px){
  .kpi-row{grid-template-columns:repeat(2,1fr)}
  .kpi:nth-child(5){grid-column:span 2}
  .g2{grid-template-columns:1fr}
  .cc.full{grid-column:span 1}
  .page{padding:16px 10px 60px}
  .hdr h1{font-size:17px}
  .kpi .v{font-size:18px}
  .hdr{flex-direction:column;align-items:flex-start}
}
</style>
</head>
<body>

<div id="login">
  <div class="lbox">
    <h2>LANOMIZ</h2>
    <div class="sub">店铺运营看板</div>
    <input type="password" id="pwd" placeholder="请输入密码" autocomplete="off">
    <button onclick="go()">进入</button>
    <div class="err" id="err">密码错误</div>
  </div>
</div>

<div id="dash">
  <div class="page">
    <div class="hdr">
      <h1>LANOMIZ 运营看板</h1>
      <div class="date-pick">
        <select id="daysel" onchange="switchDay(this.value)"></select>
        <div class="upd">更新 __UPDATED__</div>
      </div>
    </div>
    <div class="kpi-row" id="kpi"></div>

    <div class="sec">
      <div class="sec-t">14天趋势</div>
      <div class="cc full"><canvas id="trendC" style="width:100%;height:200px"></canvas></div>
    </div>

    <div class="sec">
      <div class="sec-t" id="dayTitle">当日订单分析</div>
      <div class="g2">
        <div class="cc"><h3>订单状态</h3><canvas id="pie1" height="200"></canvas><div class="leg" id="leg1"></div></div>
        <div class="cc"><h3>支付方式</h3><canvas id="pie2" height="200"></canvas><div class="leg" id="leg2"></div></div>
        <div class="cc"><h3>尺码分布</h3><canvas id="pie3" height="200"></canvas><div class="leg" id="leg3"></div></div>
        <div class="cc"><h3>颜色分布</h3><canvas id="pie4" height="200"></canvas><div class="leg" id="leg4"></div></div>
        <div class="cc"><h3>下单时段</h3><canvas id="hourC" style="width:100%;height:200px"></canvas></div>
        <div class="cc"><h3>地区 Top 10</h3><div id="regionB"></div></div>
        <div class="cc full"><h3>热卖商品</h3><div id="prodB"></div></div>
      </div>
    </div>

    <div class="sec">
      <div class="sec-t">样品申请（全量）</div>
      <div class="g2">
        <div class="cc"><h3>样品状态</h3><canvas id="pie5" height="200"></canvas><div class="leg" id="leg5"></div></div>
        <div class="cc"><h3>合作机构</h3><div class="pl" id="partL"></div></div>
      </div>
    </div>
  </div>
</div>

<script>
const PH="__PASSWORD_HASH__";
async function sha256(s){const d=new TextEncoder().encode(s);const h=await crypto.subtle.digest('SHA-256',d);return[...new Uint8Array(h)].map(b=>b.toString(16).padStart(2,'0')).join('')}
async function go(){
  if(await sha256(document.getElementById('pwd').value)===PH){
    document.getElementById('login').style.display='none';
    document.getElementById('dash').style.display='block';
    try{sessionStorage.setItem('a','1')}catch(e){}
    requestAnimationFrame(()=>requestAnimationFrame(init));
  }else document.getElementById('err').style.display='block';
}
document.getElementById('pwd').addEventListener('keydown',e=>{if(e.key==='Enter')go()});
try{if(sessionStorage.getItem('a')==='1'){document.getElementById('login').style.display='none';document.getElementById('dash').style.display='block';if(document.readyState!=='loading')requestAnimationFrame(()=>requestAnimationFrame(init));else window.addEventListener('DOMContentLoaded',()=>requestAnimationFrame(()=>requestAnimationFrame(init)))}}catch(e){}

const D=__DATA__;
const CS=['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#4a3aa7','#e34948','#008300','#9085e9','#d55181'];
const dk=window.matchMedia('(prefers-color-scheme:dark)').matches;
const surfCol=dk?'#282422':'#FFF';
const txtCol=dk?'#F5F0EB':'#1C1A18';
const mutCol=dk?'#7A746E':'#9E9890';
const gridCol=dk?'#3A3632':'#EDE8E3';
const acCol=dk?'#E0758B':'#C4536A';
const barBg=dk?'rgba(57,135,229,0.3)':'rgba(42,120,214,0.2)';

function fmt(n){if(n>=1e6)return(n/1e6).toFixed(1)+'M';if(n>=1e3)return(n/1e3).toFixed(1)+'K';return Math.round(n).toLocaleString()}

function initCanvas(c,fallbackH){
  const dpr=window.devicePixelRatio||1;
  const w=c.parentElement.clientWidth||c.clientWidth||300;
  const h=c.clientHeight||fallbackH||200;
  c.width=w*dpr;c.height=h*dpr;
  const ctx=c.getContext('2d');ctx.scale(dpr,dpr);
  return{ctx,w,h,dpr};
}

function pie(cid,lid,obj,colors){
  const c=document.getElementById(cid);
  const{ctx,w,h}=initCanvas(c,200);
  const cx=w/2,cy=h/2,R=Math.min(w,h)/2-16;
  const ent=Object.entries(obj).sort((a,b)=>b[1]-a[1]);
  const tot=ent.reduce((s,[,v])=>s+v,0);
  if(!tot){ctx.fillStyle=mutCol;ctx.font='13px sans-serif';ctx.textAlign='center';ctx.fillText('无数据',cx,cy);return}
  let ang=-Math.PI/2;
  const sl=[];
  ent.forEach(([lb,val],i)=>{
    const sw=val/tot*Math.PI*2,col=colors[i%colors.length];
    ctx.beginPath();ctx.moveTo(cx,cy);ctx.arc(cx,cy,R,ang,ang+sw);ctx.closePath();
    ctx.fillStyle=col;ctx.fill();
    ctx.strokeStyle=surfCol;ctx.lineWidth=2;ctx.stroke();
    if(val/tot>0.07){
      const mid=ang+sw/2,lx=cx+Math.cos(mid)*R*.62,ly=cy+Math.sin(mid)*R*.62;
      ctx.fillStyle=txtCol;ctx.font='bold 11px sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText(Math.round(val/tot*100)+'%',lx,ly);
    }
    sl.push({lb,val,col});ang+=sw;
  });
  const lg=document.getElementById(lid);
  if(lg) lg.innerHTML=sl.map(s=>`<div class="li"><div class="ld" style="background:${s.col}"></div>${s.lb} ${s.val}</div>`).join('');
}

function bars(id,ent,color){
  const el=document.getElementById(id);
  const mx=Math.max(...ent.map(e=>e[1]),1);
  el.innerHTML=ent.map(([l,v])=>{
    const p=(v/mx*100).toFixed(1);
    return`<div class="hb"><div class="hb-l"><span>${l}</span><span>${v}</span></div><div class="hb-t"><div class="hb-f" style="width:${p}%;background:${color}"></div></div></div>`;
  }).join('');
}

function drawTrend(daily,selDay){
  const c=document.getElementById('trendC');
  const{ctx,w,h}=initCanvas(c,200);
  const pad={t:16,r:56,b:44,l:36};
  const cw=w-pad.l-pad.r,ch=h-pad.t-pad.b;
  const n=daily.length;
  const maxO=Math.max(...daily.map(d=>d.orders),1);
  const maxR=Math.max(...daily.map(d=>d.revenue),1);

  ctx.strokeStyle=gridCol;ctx.lineWidth=1;
  for(let i=0;i<4;i++){const y=pad.t+ch*i/3;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(pad.l+cw,y);ctx.stroke()}

  const bw=cw/n*.45;
  daily.forEach((d,i)=>{
    const x=pad.l+cw*(i+.5)/n;
    const bh=d.orders/maxO*ch;
    const isSel=d.d===selDay.slice(5);
    ctx.fillStyle=isSel?'rgba(196,83,106,0.35)':barBg;
    ctx.fillRect(x-bw/2,pad.t+ch-bh,bw,bh);
  });

  ctx.strokeStyle=acCol;ctx.lineWidth=2;ctx.lineJoin='round';ctx.lineCap='round';ctx.beginPath();
  daily.forEach((d,i)=>{const x=pad.l+cw*(i+.5)/n,y=pad.t+ch-d.revenue/maxR*ch;if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)});
  ctx.stroke();

  ctx.fillStyle=dk?'rgba(224,117,139,.08)':'rgba(196,83,106,.06)';ctx.beginPath();
  daily.forEach((d,i)=>{const x=pad.l+cw*(i+.5)/n,y=pad.t+ch-d.revenue/maxR*ch;if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)});
  ctx.lineTo(pad.l+cw*(n-.5)/n+cw/n/2,pad.t+ch);ctx.lineTo(pad.l+cw*.5/n,pad.t+ch);ctx.closePath();ctx.fill();

  const last=daily[n-1],lx=pad.l+cw*(n-.5)/n,ly=pad.t+ch-last.revenue/maxR*ch;
  ctx.fillStyle=surfCol;ctx.beginPath();ctx.arc(lx,ly,5,0,Math.PI*2);ctx.fill();
  ctx.fillStyle=acCol;ctx.beginPath();ctx.arc(lx,ly,3.5,0,Math.PI*2);ctx.fill();

  ctx.fillStyle=mutCol;ctx.font='10px sans-serif';ctx.textAlign='center';
  const step=w<500?4:2;daily.forEach((d,i)=>{if(i%step===0||i===n-1){ctx.fillText(d.d,pad.l+cw*(i+.5)/n,pad.t+ch+14)}});
  ctx.textAlign='right';ctx.textBaseline='middle';
  for(let i=0;i<4;i++){ctx.fillText(fmt(maxO*(3-i)/3),pad.l-4,pad.t+ch*i/3)}
  ctx.textAlign='left';ctx.fillStyle=acCol;
  for(let i=0;i<4;i++){ctx.fillText('$'+fmt(maxR*(3-i)/3),pad.l+cw+4,pad.t+ch*i/3)}

  const ly0=h-6;const lx0=pad.l;ctx.fillStyle=barBg.replace('0.3','0.6').replace('0.2','0.5');ctx.fillRect(lx0,ly0-6,10,8);
  ctx.fillStyle=mutCol;ctx.font='10px sans-serif';ctx.textAlign='left';ctx.fillText('订单',lx0+14,ly0);
  ctx.strokeStyle=acCol;ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(lx0+46,ly0-2);ctx.lineTo(lx0+58,ly0-2);ctx.stroke();
  ctx.fillStyle=mutCol;ctx.fillText('销售额',lx0+62,ly0);
}

function drawHours(hours){
  const c=document.getElementById('hourC');
  const{ctx,w,h}=initCanvas(c,200);
  const pad={t:10,r:10,b:24,l:28};
  const cw=w-pad.l-pad.r,ch=h-pad.t-pad.b;
  const vals=[];for(let i=0;i<24;i++)vals.push(parseInt(hours[i]||0));
  const mx=Math.max(...vals,1);
  const bw=cw/24*.7;

  ctx.strokeStyle=gridCol;ctx.lineWidth=1;
  for(let i=0;i<3;i++){const y=pad.t+ch*i/2;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(pad.l+cw,y);ctx.stroke()}

  vals.forEach((v,i)=>{
    const x=pad.l+cw*(i+.5)/24;
    const bh=v/mx*ch;
    ctx.fillStyle=CS[0];
    ctx.globalAlpha=0.7;
    ctx.fillRect(x-bw/2,pad.t+ch-bh,bw,bh);
    ctx.globalAlpha=1;
  });

  ctx.fillStyle=mutCol;ctx.font='9px sans-serif';ctx.textAlign='center';
  for(let i=0;i<24;i+=2){ctx.fillText(i+'',pad.l+cw*(i+.5)/24,h-4)}
  ctx.textAlign='right';ctx.textBaseline='middle';
  for(let i=0;i<3;i++){ctx.fillText(fmt(mx*(2-i)/2),pad.l-3,pad.t+ch*i/2)}
}

let curDay;
function switchDay(d){
  curDay=d;
  renderDay(d);
}

function renderDay(day){
  const dd=D.days[day];
  if(!dd)return;

  document.getElementById('dayTitle').textContent=day+' 订单分析（墨西哥时间）';

  // Find previous day for comparison
  const idx=D.available_days.indexOf(day);
  const prevDay=idx<D.available_days.length-1?D.available_days[idx+1]:null;
  const prev=prevDay?D.days[prevDay]:null;

  function delta(cur,prv){
    if(!prv||!prv)return'';
    if(prv===0)return cur>0?' <span class="up">↑</span>':'';
    const pct=((cur-prv)/prv*100).toFixed(0);
    return pct>=0?` <span class="up">↑${pct}%</span>`:` <span class="dn">↓${Math.abs(pct)}%</span>`;
  }

  document.getElementById('kpi').innerHTML=[
    {l:'订单',v:dd.total,d:`有效 ${dd.valid} / 取消 ${dd.cancelled}`+delta(dd.total,prev?.total)},
    {l:'销售额',v:'MX$'+fmt(dd.revenue),d:'有效订单'+delta(dd.revenue,prev?.revenue)},
    {l:'商品件数',v:dd.items,d:'有效订单'},
    {l:'均单价',v:'MX$'+fmt(dd.avg_order),d:''},
    {l:'货到付款',v:dd.cod_rate+'%',d:`样品单 ${dd.sample_orders}`},
  ].map(k=>`<div class="kpi"><div class="l">${k.l}</div><div class="v">${k.v}</div><div class="d">${k.d}</div></div>`).join('');

  setTimeout(()=>{
    drawTrend(D.trend,day);
    pie('pie1','leg1',dd.status,CS);
    pie('pie2','leg2',dd.payment,CS);
    pie('pie3','leg3',dd.sizes,CS);
    pie('pie4','leg4',Object.fromEntries(dd.colors),CS);
    drawHours(dd.hours);
    bars('regionB',dd.regions,'var(--c1)');
    bars('prodB',dd.products,'var(--c3)');
  },100);
}

function init(){
  const sel=document.getElementById('daysel');
  D.available_days.forEach((d,i)=>{
    const o=document.createElement('option');
    o.value=d;
    o.textContent=d+(i===0?' (今天)':i===1?' (昨天)':'');
    sel.appendChild(o);
  });
  sel.value=D.default_day;
  curDay=D.default_day;

  // Samples (always show full)
  setTimeout(()=>{
    pie('pie5','leg5',D.samples.status,CS);
    document.getElementById('partL').innerHTML=D.samples.partners.map(p=>
      `<div class="pc"><div class="nm">${p.name}</div><div class="inf">${p.creators}达人 · ${p.apps}次申请</div></div>`
    ).join('')||'<div style="color:var(--t3)">无机构</div>';
  },80);

  renderDay(curDay);
}
</script>
</body>
</html>
''';


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    if os.environ.get("TK_APP_KEY"):
        keys_content = "\n".join(
            f"{k}={v}" for k, v in {
                "app_key": os.environ["TK_APP_KEY"],
                "app_secret": os.environ["TK_APP_SECRET"],
                "access_token": os.environ["TK_ACCESS_TOKEN"],
                "refresh_token": os.environ["TK_REFRESH_TOKEN"],
                "access_token_expire_in": os.environ.get("TK_ACCESS_TOKEN_EXPIRE", "0"),
                "refresh_token_expire_in": os.environ.get("TK_REFRESH_TOKEN_EXPIRE", "0"),
            }.items()
        )
        keys_file = Path(MONITOR_DIR) / "tk_shop_keys.txt"
        keys_file.parent.mkdir(parents=True, exist_ok=True)
        keys_file.write_text(keys_content + "\n", encoding="utf-8")

    sys.stderr.write("Starting dashboard generation...\n")
    keys = shop_report.refresh_token_if_needed(shop_report.load_keys())
    cipher = shop_report.get_shop_cipher(keys)
    data = collect_data(keys, cipher)

    now_mx = datetime.now(MX_TZ)
    updated_at = now_mx.strftime("%Y-%m-%d %H:%M") + " (墨西哥时间)"

    output_dir = SCRIPT_DIR / "docs"
    output_dir.mkdir(exist_ok=True)
    html = build_html(data, updated_at)
    (output_dir / "index.html").write_text(html, encoding="utf-8")
    sys.stderr.write(f"Dashboard written to {output_dir / 'index.html'}\n")

    if os.environ.get("GITHUB_OUTPUT"):
        keys = shop_report.load_keys()
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            for k in ("access_token", "refresh_token", "access_token_expire_in", "refresh_token_expire_in"):
                f.write(f"{k}={keys.get(k,'')}\n")


if __name__ == "__main__":
    main()
