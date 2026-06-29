/* ===================== Nova — charts ===================== */
const { useState:_useState } = React;

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
function RadarChart({axes, thisMonth, lastMonth, compareWith=null, size=300}){
  const cx=size/2, cy=size/2+8, R=size*0.34, n=axes.length;
  const angle = i => (-90 + i*(360/n)) * Math.PI/180;
  const pt = (i,val) => [cx + Math.cos(angle(i))*R*(val/100), cy + Math.sin(angle(i))*R*(val/100)];
  const poly = arr => arr.map((v,i)=>pt(i,v).join(',')).join(' ');
  const rings=[25,50,75,100];
  return h('svg',{viewBox:`0 0 ${size} ${size}`, width:'100%', style:{maxWidth:size, display:'block', margin:'0 auto'}},
    h('defs',null,
      h('linearGradient',{id:'radarFill', x1:'0',y1:'0',x2:'1',y2:'1'},
        h('stop',{offset:'0%', stopColor:'#A634FF', stopOpacity:.34}),
        h('stop',{offset:'100%', stopColor:'#FF4398', stopOpacity:.22}))
    ),
    // grid rings
    rings.map((r,ri)=>h('polygon',{key:'r'+ri,
      points:axes.map((_,i)=>pt(i,r).join(',')).join(' '),
      fill:'none', stroke:'#e7e6f0', strokeWidth:1})),
    // spokes + labels
    axes.map((ax,i)=>{
      const [x,y]=pt(i,100); const [lx,ly]=pt(i,124);
      return h('g',{key:'ax'+i},
        h('line',{x1:cx,y1:cy,x2:x,y2:y, stroke:'#e7e6f0', strokeWidth:1}),
        h('text',{x:lx,y:ly+4, textAnchor:'middle', fontSize:13, fontWeight:700, fill:'#7a7d96'}, ax)
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
      yticks.map(t=>h('g',{key:'y'+t},
        h('line',{x1:padL,y1:iy(t),x2:W-padR,y2:iy(t),stroke:'#eeedf5',strokeWidth:1}),
        h('text',{x:padL-10,y:iy(t)+4,textAnchor:'end',fontSize:11.5,fontWeight:600,fill:'#9a9db4'}, t+'%')
      )),
      // target line
      h('line',{x1:padL,y1:iy(target),x2:W-padR,y2:iy(target),stroke:'#FF4398',strokeWidth:1.6,strokeDasharray:'6 5',opacity:.55}),
      h('text',{x:W-padR,y:iy(target)-7,textAnchor:'end',fontSize:11,fontWeight:800,fill:'#FF4398'}, `Target ${target}%`),
      // x labels
      months.map((m,i)=>h('text',{key:'x'+i,x:ix(i),y:H-padB+22,textAnchor:'middle',fontSize:11.5,fontWeight:600,fill:'#9a9db4'}, m)),
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
      style:{left:`${(ix(hover)/W)*100}%`, top:`${(iy(proficiency[hover])/H)*100}%`}},
      h('div',{className:'t1'}, months[hover]),
      h('div',{className:'row'}, h('span',{className:'dot',style:{background:'#A634FF'}}),
        `AI-proficient ${proficiency[hover]}% · ${Math.round(total*proficiency[hover]/100).toLocaleString()}`),
      h('div',{className:'row'}, h('span',{className:'dot',style:{background:'#2ACCFF'}}),
        `Active ${active[hover]}%`)
    )
  );
}

Object.assign(window,{TierTrack, RadarChart, LineChart});
