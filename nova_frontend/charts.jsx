/* ===================== Nova — charts ===================== */
const { useState:_useState } = React;

/* Clamp a chart tooltip so it never clips at the card edges. Given the point's
   fractional position (0–1) in the viewBox, returns {left, top, transform}:
   anchors the tip left/center/right near the sides and flips it below the point
   near the top. Shared by every chart (and VerticalBars in manager.jsx). */
function _tipStyle(fx, fy){
  fx = Math.max(0, Math.min(1, fx));
  fy = Math.max(0, Math.min(1, fy));
  const tx = fx < 0.18 ? '4px' : fx > 0.82 ? 'calc(-100% - 4px)' : '-50%';
  const ty = fy < 0.22 ? '10px' : '-115%';
  return { left: (fx*100)+'%', top: (fy*100)+'%', transform: `translate(${tx}, ${ty})` };
}

/* Shared y-axis gridline + %-suffixed label row, used by LineChart,
   RegionProficiencyChart, ScatterChart, and QuadrantChart. */
function YAxisGrid({yticks, iy, padL, padR, W, fontSize=11.5, labelOffset=10}){
  return yticks.map(t=>h('g',{key:'y'+t},
    h('line',{x1:padL,y1:iy(t),x2:W-padR,y2:iy(t),stroke:'var(--chart-grid)',strokeWidth:1}),
    h('text',{x:padL-labelOffset,y:iy(t)+4,textAnchor:'end',fontSize,fontWeight:600,fill:'var(--chart-label)'}, t+'%')
  ));
}

/* Shared day-labeled x-axis ticks, used by ScatterChart and QuadrantChart. */
function XDayTicks({xticks, ix, y, fontSize=11.5}){
  return xticks.map(t=>h('text',{key:'x'+t,x:ix(t),y,textAnchor:'middle',fontSize,fontWeight:600,fill:'var(--chart-label)'}, t+'d'));
}

/* ---------- Tier progression track ---------- */
function TierTrack({tiers, currentKey}){
  const curIdx = tiers.findIndex(t=>t.key===currentKey);
  return h('div',{className:'tier-track'},
    h('div',{className:'tier-line'}),
    tiers.map((t,i)=>{
      const active = i===curIdx;
      const past = i<curIdx;
      const col = (active||past)? t.color : '#c7c9d6';
      return h('div',{className:'tier-node'+(active?' cur':''), key:t.key},
        h(Hex,{color:col, glyph:tierHexInner(t.key), active, size:62}),
        h('div',{className:'nm', style: active?{color:t.color}:null}, t.name)
      );
    })
  );
}

/* ---------- Radar / skill chart ---------- */
function RadarChart({axes, thisMonth, lastMonth, compareWith=null, size=300, labelValues=null}){
  const cx=size/2, cy=size/2+8, R=size*0.34, n=axes.length;
  const angle = i => (-90 + i*(360/n)) * Math.PI/180;
  const pt = (i,val) => [cx + Math.cos(angle(i))*R*(val/100), cy + Math.sin(angle(i))*R*(val/100)];
  const poly = arr => arr.map((v,i)=>pt(i,v).join(',')).join(' ');
  const rings=[25,50,75,100];
  return h('svg',{viewBox:`0 0 ${size} ${size}`, width:'100%', style:{maxWidth:size, display:'block', margin:'0 auto', overflow:'visible'}},
    h('defs',null,
      h('linearGradient',{id:'radarFill', x1:'0',y1:'0',x2:'1',y2:'1'},
        h('stop',{offset:'0%', stopColor:'#A634FF', stopOpacity:.34}),
        h('stop',{offset:'100%', stopColor:'#FF4398', stopOpacity:.22}))
    ),
    // grid rings
    rings.map((r,ri)=>h('polygon',{key:'r'+ri,
      points:axes.map((_,i)=>pt(i,r).join(',')).join(' '),
      fill:'none', stroke:'var(--chart-grid)', strokeWidth:1})),
    // spokes + labels (+ optional per-axis % under the label)
    axes.map((ax,i)=>{
      const [x,y]=pt(i,100); const [lx,ly]=pt(i,124);
      return h('g',{key:'ax'+i},
        h('line',{x1:cx,y1:cy,x2:x,y2:y, stroke:'var(--chart-grid)', strokeWidth:1}),
        h('text',{x:lx,y:ly+4, textAnchor:'middle', fontSize:13, fontWeight:700, fill:'var(--chart-label)'}, ax),
        labelValues ? h('text',{x:lx,y:ly+20, textAnchor:'middle', fontSize:12.5, fontWeight:800, fill:'var(--muted-2)'}, Math.round(labelValues[i])+'%') : null
      );
    }),
    // last month (dashed blue)
    h('polygon',{points:poly(lastMonth), fill:'rgba(42,204,255,.08)', stroke:'#2ACCFF', strokeWidth:2, strokeDasharray:'5 4'}),
    // teammate compare ring (faint green dashed) — drawn before thisMonth so it sits behind
    compareWith && h('polygon',{points:poly(compareWith), fill:'rgba(31,169,113,.07)', stroke:'#1FA971', strokeWidth:1.8, strokeDasharray:'4 4'}),
    // this month (gradient)
    h('polygon',{points:poly(thisMonth), fill:'url(#radarFill)', stroke:'#A634FF', strokeWidth:2.4}),
    thisMonth.map((v,i)=>{const[x,y]=pt(i,v);return h('circle',{key:'d'+i,cx:x,cy:y,r:3.4,fill:'#A634FF',stroke:'#fff',strokeWidth:1.6})})
  );
}

/* ---------- Manager line chart ---------- */
function LineChart({months, proficiency, active, target, total}){
  const [hover,setHover]=_useState(null);
  const W=820, H=320, padL=44, padR=20, padT=22, padB=40;
  const ix = i => padL + i*( (W-padL-padR)/(months.length-1) );
  const iy = v => padT + (100-v)/100 * (H-padT-padB);
  const linePath = arr => arr.map((v,i)=>`${i?'L':'M'}${ix(i)},${iy(v)}`).join(' ');
  const areaPath = arr => linePath(arr)+`L${ix(arr.length-1)},${H-padB}L${ix(0)},${H-padB}Z`;
  const yticks=[0,25,50,75,100];

  return h('div',{style:{position:'relative'}},
    h('svg',{viewBox:`0 0 ${W} ${H}`, width:'100%', onMouseLeave:()=>setHover(null)},
      h('defs',null,
        h('linearGradient',{id:'profArea',x1:'0',y1:'0',x2:'0',y2:'1'},
          h('stop',{offset:'0%',stopColor:'#A634FF',stopOpacity:.22}),
          h('stop',{offset:'100%',stopColor:'#A634FF',stopOpacity:0})),
        h('linearGradient',{id:'profLine',x1:'0',y1:'0',x2:'1',y2:'0'},
          h('stop',{offset:'0%',stopColor:'#2ACCFF'}),
          h('stop',{offset:'55%',stopColor:'#A634FF'}),
          h('stop',{offset:'100%',stopColor:'#FF4398'}))
      ),
      // y grid + labels
      YAxisGrid({yticks, iy, padL, padR, W}),
      // target line
      h('line',{x1:padL,y1:iy(target),x2:W-padR,y2:iy(target),stroke:'#FF4398',strokeWidth:1.6,strokeDasharray:'6 5',opacity:.55}),
      h('text',{x:W-padR,y:iy(target)-7,textAnchor:'end',fontSize:11,fontWeight:800,fill:'#FF4398'}, `Target ${target}%`),
      // x labels
      months.map((m,i)=>h('text',{key:'x'+i,x:ix(i),y:H-padB+22,textAnchor:'middle',fontSize:11.5,fontWeight:600,fill:'var(--chart-label)'}, m)),
      // active learners (dashed)
      h('path',{d:linePath(active),fill:'none',stroke:'#2ACCFF',strokeWidth:2.4,strokeDasharray:'6 5',strokeLinecap:'round'}),
      // proficiency
      h('path',{d:areaPath(proficiency),fill:'url(#profArea)'}),
      h('path',{d:linePath(proficiency),fill:'none',stroke:'url(#profLine)',strokeWidth:3.4,strokeLinecap:'round',strokeLinejoin:'round'}),
      // dots on proficiency
      proficiency.map((v,i)=>h('circle',{key:'p'+i,cx:ix(i),cy:iy(v),r:hover===i?5.5:3.4,fill:'#fff',stroke:'#A634FF',strokeWidth:2.4})),
      hover!==null && h('line',{x1:ix(hover),y1:padT,x2:ix(hover),y2:H-padB,stroke:'#A634FF',strokeWidth:1,opacity:.25}),
      // hit areas
      months.map((m,i)=>h('rect',{key:'h'+i,x:ix(i)-((W-padL-padR)/(months.length-1))/2,y:0,
        width:(W-padL-padR)/(months.length-1),height:H,fill:'transparent',onMouseEnter:()=>setHover(i)}))
    ),
    hover!==null && h('div',{className:'chart-tip',
      style:_tipStyle(ix(hover)/W, iy(proficiency[hover])/H)},
      h('div',{className:'t1'}, months[hover]),
      h('div',{className:'row'}, h('span',{className:'dot',style:{background:'#A634FF'}}),
        `AI-proficient ${proficiency[hover]}% · ${Math.round(total*proficiency[hover]/100).toLocaleString()}`),
      h('div',{className:'row'}, h('span',{className:'dot',style:{background:'#2ACCFF'}}),
        `Active ${active[hover]}%`)
    )
  );
}

/* ---------- Manager region proficiency bar chart ---------- */
/* Grouped bars per AI-proficiency level (Professional→Champion): one bar per
   region, height = that region's OWN % proficient at the level (independent
   of headcount, so a small region isn't visually crushed by a big one). A
   dashed line marks the company-wide goal for the level; a solid grey tick
   marks the company-wide actual %. Hover a bar → exact counts. All numbers
   come straight from the backend payload. */
function RegionProficiencyChart({data}){
  const [hover,setHover]=_useState(null);   // {li, rkey}
  if(!data || !data.levels || !data.levels.length){
    return h('div',{style:{padding:'48px 0',textAlign:'center',color:'var(--muted)',
      fontSize:14,fontWeight:600}}, 'Computing region breakdown…');
  }
  const {levels, regions} = data;
  const fmt = n => Math.round(n).toLocaleString();
  const nReg = regions.length;

  const W=820, H=380, padL=44, padR=20, padT=30, padB=70;
  const plotH=H-padT-padB;
  const colW=(W-padL-padR)/levels.length;
  const groupW=colW*0.74;
  const barGap=6;
  const barW=Math.max(14,(groupW-(nReg-1)*barGap)/nReg);
  const cx = i => padL + colW*i + colW/2;
  const groupLeft = i => cx(i)-groupW/2;
  const iy = v => padT + (100-v)/100*plotH;   // v in 0..100 (% of that region's own employees)
  const yticks=[0,25,50,75,100];

  const regionByKey = Object.fromEntries(regions.map(r=>[r.key,r]));

  return h('div',{style:{position:'relative'}},
    h('svg',{viewBox:`0 0 ${W} ${H}`, width:'100%', onMouseLeave:()=>setHover(null)},
      // y grid + labels
      YAxisGrid({yticks, iy, padL, padR, W}),
      // per-level groups
      levels.map((lv,li)=>{
        const gl=groupLeft(li);
        const bars=regions.map((r,ri)=>{
          const seg=lv.regions[r.key]||{};
          const pct=seg.pct_of_region||0;
          const x=gl+ri*(barW+barGap);
          const yTop=iy(pct), yBot=iy(0);
          const isActive=hover && hover.li===li && hover.rkey===r.key;
          return h('g',{key:'b'+r.key},
            h('rect',{x, y:yTop, width:barW, height:Math.max(0,yBot-yTop),
              fill:r.color, opacity:isActive?1:0.88,
              stroke:isActive?'#fff':'rgba(255,255,255,.22)', strokeWidth:isActive?2:1,
              style:{cursor:'pointer',transition:'opacity .15s'},
              onMouseEnter:()=>setHover({li,rkey:r.key}), onMouseLeave:()=>setHover(null)}),
            pct>0 && h('text',{x:x+barW/2, y:yTop-5, textAnchor:'middle',
              fontSize:9.5,fontWeight:800,fill:r.color}, Math.round(pct)+'%')
          );
        });

        // company-wide actual — solid grey tick across the group
        const cy=iy(lv.totalPct);
        const companyTick=h('g',{key:'co'},
          h('line',{x1:gl-4,y1:cy,x2:gl+groupW+4,y2:cy, stroke:'var(--muted)',strokeWidth:2}),
          h('circle',{cx:gl-4,cy,r:2.5,fill:'var(--muted)'}),
          h('circle',{cx:gl+groupW+4,cy,r:2.5,fill:'var(--muted)'}));

        // goal — dashed line above/below, spanning a bit wider than the group
        const gy=iy(lv.goalPct);
        const gw=groupW+20;
        const goal=h('g',{key:'g'},
          h('line',{x1:cx(li)-gw/2,y1:gy,x2:cx(li)+gw/2,y2:gy,
            stroke:'var(--chart-label)',strokeWidth:1.8,strokeDasharray:'5 4',opacity:.85}),
          h('text',{x:cx(li)+gw/2,y:gy-5,textAnchor:'end',fontSize:10.5,fontWeight:800,
            fill:'var(--chart-label)'}, 'Goal '+lv.goalPct+'%'));

        // x-axis level name + company-wide summary
        const xlab=h('g',{key:'xl'},
          h('text',{x:cx(li),y:H-padB+22,textAnchor:'middle',fontSize:13.5,fontWeight:800,
            fill:'var(--ink)'}, lv.name),
          h('text',{x:cx(li),y:H-padB+39,textAnchor:'middle',fontSize:11,fontWeight:600,
            fill:'var(--chart-label)'}, 'Company '+lv.totalPct+'% · '+fmt(lv.totalCount)));

        return h('g',{key:'col'+li}, bars, companyTick, goal, xlab);
      })
    ),
    hover && (()=>{
      const lv=levels[hover.li];
      const r=regionByKey[hover.rkey];
      const seg=lv.regions[hover.rkey]||{};
      const lx=(groupLeft(hover.li)+(r? regions.indexOf(r)*(barW+barGap):0)+barW/2)/W;
      const ty=iy(seg.pct_of_region||0)/H;
      return h('div',{className:'chart-tip',style:_tipStyle(lx, ty)},
        h('div',{className:'t1'}, (r?r.label:hover.rkey)+' · '+lv.name),
        h('div',{className:'row'}, h('span',{className:'dot',style:{background:r?r.color:'#999'}}),
          (seg.pct_of_region||0)+'% of '+(r?r.label:'')+' at '+lv.name),
        h('div',{style:{fontSize:12,color:'#b7b9cc',fontWeight:600,marginTop:4}},
          fmt(seg.count||0)+' of '+fmt(r?r.total:0)+' '+(r?r.label:'')+' employees'));
    })()
  );
}

/* ---------- Hollow donut (team badges by tier) ---------- */
/* segments = [{name,color,value}]. Renders a hollow ring (one arc per segment),
   the total in the center, per-segment counts around the ring, and a color key
   to the right. Handles the all-zero case (empty track + "0"). */
function DonutChart({segments, centerValue, centerLabel, size=180}){
  const cx=size/2, cy=size/2, sw=18, r=(size-sw)/2 - 2;
  const C=2*Math.PI*r;
  const total=segments.reduce((s,x)=>s+(x.value||0),0);

  const arcs=[];
  if(total>0){
    let cum=0;
    segments.forEach((seg,i)=>{
      const v=seg.value||0;
      if(v<=0) return;
      const len=v/total*C;
      arcs.push(h('circle',{key:'a'+i, cx, cy, r, fill:'none', stroke:seg.color,
        strokeWidth:sw, strokeDasharray:`${len} ${C-len}`, strokeDashoffset:-cum, strokeLinecap:'butt'}));
      cum+=len;
    });
  }

  return h('div',{style:{display:'flex',alignItems:'center',gap:20,flexWrap:'wrap',justifyContent:'center'}},
    h('svg',{viewBox:`0 0 ${size} ${size}`, width:size, height:size, style:{overflow:'visible',flex:'0 0 auto'}},
      h('circle',{cx,cy,r,fill:'none',stroke:'var(--surface-3)',strokeWidth:sw}),
      h('g',{transform:`rotate(-90 ${cx} ${cy})`}, arcs),
      h('text',{x:cx,y:cy-2,textAnchor:'middle',fontSize:30,fontWeight:800,fill:'var(--ink)'}, _fmtNum(centerValue)),
      centerLabel ? h('text',{x:cx,y:cy+18,textAnchor:'middle',fontSize:12,fontWeight:600,fill:'var(--muted)'}, centerLabel) : null
    ),
    // color key
    h('div',{style:{display:'flex',flexDirection:'column',gap:10,minWidth:110}},
      segments.map((seg,i)=>h('div',{key:i,style:{display:'flex',alignItems:'center',gap:9,fontSize:12.5,fontWeight:700,color:'var(--ink-soft)'}},
        h('span',{style:{width:11,height:11,borderRadius:'50%',background:seg.color,flex:'0 0 auto'}}),
        h('span',{style:{flex:1}}, seg.name),
        h('span',{style:{fontWeight:800,color:'var(--ink)'}}, _fmtNum(seg.value||0)))))
  );
}
function _fmtNum(n){ return Math.round(n||0).toLocaleString(); }

/* ---------- Team Landscape scatter (Your Team page) ----------
   One dot per direct report: x = total all-time active days, y = AI proficiency
   (0-100). Hover → name tooltip; click → onSelect(id) for two-way highlight with
   the people table. Sized compactly for the narrow rail (~320-360px). */
function ScatterChart({points, selectedId, onSelect, compact}){
  const [hover,setHover]=_useState(null);
  const pts = points || [];
  const W=360, H=300, padL=52, padR=16, padT=16, padB=42;
  const Y_TOP = 110;   // proficiency axis: 0–100 ticks + headroom so top dots aren't clipped
  const maxDays = Math.max(5, ...pts.map(p=>p.x||0));
  const xTop = Math.ceil(maxDays) + 1;
  const ix = x => padL + (Math.max(0,x)/xTop) * (W-padL-padR);
  const iy = y => padT + (1 - Math.min(100,Math.max(0,y))/Y_TOP) * (H-padT-padB);
  const yticks=[0,25,50,75,100];
  const xSteps = 5;
  const xticks=[]; for(let i=0;i<=xSteps;i++) xticks.push(Math.round(xTop*i/xSteps));
  const uniqX = [...new Set(xticks)];
  const fs = compact ? 9.5 : 11.5;
  const midY=(padT+(H-padB))/2;

  if(!pts.length){
    return h('div',{style:{padding:'28px 6px',color:'var(--muted)',fontSize:13,fontWeight:600,textAlign:'center'}},'No team data yet.');
  }

  const hoverPt = hover!=null ? pts.find(p=>p.id===hover) : null;

  return h('div',{style:{position:'relative'}},
    h('svg',{viewBox:`0 0 ${W} ${H}`, width:'100%', onMouseLeave:()=>setHover(null)},
      // y grid + labels (%)
      YAxisGrid({yticks, iy, padL, padR, W, fontSize:fs, labelOffset:7}),
      // x ticks + labels (days)
      XDayTicks({xticks:uniqX, ix, y:H-padB+20, fontSize:fs}),
      // axis titles — x below (horizontal), y rotated sideways in the left gutter
      h('text',{x:(padL+(W-padR))/2,y:H-6,textAnchor:'middle',fontSize:fs,fontWeight:800,fill:'var(--chart-label)'}, 'Active days'),
      h('text',{transform:`rotate(-90 11 ${midY})`,x:11,y:midY,textAnchor:'middle',fontSize:fs,fontWeight:800,fill:'var(--chart-label)'}, 'AI proficiency'),
      // points (selected drawn last so its ring sits on top)
      pts.map(p=>{
        if(p.id===selectedId) return null;
        const isHover = p.id===hover;
        const color = p.atRisk ? '#E23D6E' : '#A634FF';
        return h('circle',{key:'pt'+p.id, cx:ix(p.x), cy:iy(p.y), r:isHover?6:4.2,
          fill:color, fillOpacity:.85, stroke:'#fff', strokeWidth:isHover?1.6:1,
          style:{cursor:'pointer',transition:'r .12s'}, onMouseEnter:()=>setHover(p.id), onMouseLeave:()=>setHover(null),
          onClick:()=>onSelect&&onSelect(p.id)});
      }),
      pts.filter(p=>p.id===selectedId).map(p=>h('circle',{key:'sel'+p.id, cx:ix(p.x), cy:iy(p.y), r:7,
        fill:(p.atRisk?'#E23D6E':'#A634FF'), stroke:'#fff', strokeWidth:2.6,
        style:{cursor:'pointer'}, onClick:()=>onSelect&&onSelect(p.id)}))
    ),
    hoverPt && h('div',{className:'chart-tip', style:_tipStyle(ix(hoverPt.x)/W, iy(hoverPt.y)/H)},
      h('div',{className:'t1'}, hoverPt.name),
      h('div',{className:'row'}, h('span',{className:'dot',style:{background:hoverPt.atRisk?'#E23D6E':'#A634FF'}}),
        `AI ${Math.round(hoverPt.y)}% · ${hoverPt.x} active day${hoverPt.x===1?'':'s'}`))
  );
}

/* ---------- Team Landscape 4-quadrant scatter ---------- */
/* Each dot = one manager's team (clustered when close): x = avg all-time active
   days, y = avg AI proficiency. Color = manager's continent. Crosshair at the
   axis midpoint splits it into 4 quadrants. Hover names the manager(s) and calls
   onHover(managerIds) so the Team Leaderboard can cross-highlight; a point is
   emphasized when any of its managers is in highlightIds. */
function QuadrantChart({data, highlightIds, onHover, onSelect}){
  const [hover,setHover]=_useState(null);
  const pts = (data && data.points) || [];
  const maxX = (data && data.maxX) || 10;
  const Y_TOP = 110;   // proficiency axis: 0–100 ticks, extra headroom so top dots aren't clipped
  const W=760, H=440, padL=64, padR=20, padT=22, padB=46;
  const ix = x => padL + (Math.max(0,x)/maxX) * (W-padL-padR);
  const iy = y => padT + (1 - Math.min(100,Math.max(0,y))/Y_TOP) * (H-padT-padB);
  const STEPS = 5;
  const xticks=[];
  for(let i=0;i<=STEPS;i++) xticks.push(Math.round(maxX*i/STEPS));
  const yticks=[0,25,50,75,100];
  const midY=(padT+(H-padB))/2;

  const hl = new Set((highlightIds||[]).map(String));
  const isHl = p => hl.has(String(p.manager_id));
  const _hover = id => { setHover(id); if(onHover){ const p=pts.find(x=>x.id===id); onHover(p?[p.manager_id]:null); } };
  const _leave = () => { setHover(null); if(onHover) onHover(null); };

  if(!pts.length){
    return h('div',{style:{padding:'40px 6px',color:'var(--muted)',fontSize:13,fontWeight:600,textAlign:'center'}},'No team data yet.');
  }

  const hoverPt = hover!=null ? pts.find(p=>p.id===hover) : null;
  // Draw highlighted/hovered points last so they sit on top.
  const ordered = [...pts].sort((a,b)=>((isHl(a)||a.id===hover)?1:0)-((isHl(b)||b.id===hover)?1:0));

  const dot = p => {
    const on = isHl(p) || p.id===hover;
    const r = on?7:4.5;
    return h('circle',{key:'pt'+p.id, cx:ix(p.x), cy:iy(p.y), r,
      fill:p.color||'#9aa2b1', fillOpacity:on?.98:.62, stroke:'#fff', strokeWidth:on?2:.8,
      style:{cursor:'pointer',transition:'r .1s'},
      onMouseEnter:()=>_hover(p.id), onMouseLeave:_leave,
      onClick:()=>onSelect&&onSelect(p.manager_id)});
  };

  return h('div',{style:{position:'relative'}},
    h('svg',{viewBox:`0 0 ${W} ${H}`, width:'100%', onMouseLeave:_leave},
      // y grid + labels (%)
      YAxisGrid({yticks, iy, padL, padR, W}),
      // x labels (days)
      XDayTicks({xticks, ix, y:H-padB+20}),
      // quadrant crosshair (days midpoint × proficiency 50)
      h('line',{x1:ix(maxX/2),y1:padT,x2:ix(maxX/2),y2:H-padB,stroke:'var(--chart-label)',strokeWidth:1.4,strokeDasharray:'6 5',opacity:.5}),
      h('line',{x1:padL,y1:iy(50),x2:W-padR,y2:iy(50),stroke:'var(--chart-label)',strokeWidth:1.4,strokeDasharray:'6 5',opacity:.5}),
      // axis titles — x below (horizontal), y rotated sideways in the left gutter
      h('text',{x:(padL+(W-padR))/2,y:H-6,textAnchor:'middle',fontSize:12,fontWeight:800,fill:'var(--chart-label)'}, 'Active days (team avg)'),
      h('text',{transform:`rotate(-90 18 ${midY})`,x:18,y:midY,textAnchor:'middle',fontSize:12,fontWeight:800,fill:'var(--chart-label)'}, 'AI proficiency'),
      // points
      ordered.map(dot)
    ),
    hoverPt && h('div',{className:'chart-tip', style:_tipStyle(ix(hoverPt.x)/W, iy(hoverPt.y)/H)},
      h('div',{className:'t1'}, hoverPt.name),
      h('div',{className:'row'}, h('span',{className:'dot',style:{background:hoverPt.color||'#9aa2b1'}}),
        `AI ${Math.round(hoverPt.y)}% · ${hoverPt.x} active days (avg)`),
      h('div',{className:'row'}, `${hoverPt.people} ${hoverPt.people===1?'person':'people'}`))
  );
}

Object.assign(window,{TierTrack, RadarChart, LineChart, RegionProficiencyChart, DonutChart, ScatterChart, QuadrantChart, _fmtNum});
