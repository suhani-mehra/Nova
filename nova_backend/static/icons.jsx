/* ===================== Nova — icons & small atoms ===================== */
const { createElement: h } = React;

/* generic stroke icon factory */
function svg(paths, props={}, vb=24){
  return (extra={}) => h('svg', {width:extra.size||22, height:extra.size||22, viewBox:`0 0 ${vb} ${vb}`,
    fill:'none', stroke:extra.color||'currentColor', strokeWidth:extra.sw||1.8,
    strokeLinecap:'round', strokeLinejoin:'round', ...props, ...(extra.style?{style:extra.style}:{})}, paths);
}
const P = (d,k)=>h('path',{d,key:k});
const C = (cx,cy,r,k)=>h('circle',{cx,cy,r,key:k});

const Icons = {
  clock: svg([C(12,12,9,'a'), P('M12 7v5l3 2','b')]),
  users: svg([P('M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1','a'), C(9,8,3.2,'b'), P('M21 19v-1a4 4 0 0 0-3-3.87','c'), P('M16 4.2a4 4 0 0 1 0 7.6','d')]),
  chevron: svg([P('M6 9l6 6 6-6','a')]),
  switch: svg([P('M16 3l4 4-4 4','a'), P('M20 7H8a4 4 0 0 0-4 4','b'), P('M8 21l-4-4 4-4','c'), P('M4 17h12a4 4 0 0 0 4-4','d')]),
  logout: svg([P('M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4','a'), P('M16 17l5-5-5-5','b'), P('M21 12H9','c')]),
  settings: svg([C(12,12,3,'a'), P('M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 7 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 2.6 14H2.5a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 7a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.6 1.6 0 0 0 10 2.6V2.5a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7h.1a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.6 1z','b')]),
  book: svg([P('M4 19.5A2.5 2.5 0 0 1 6.5 17H20','a'), P('M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z','b')]),
  clap: svg([P('M11 11l-1.6-1.6a1.5 1.5 0 0 0-2.1 2.1l3.3 3.3','a'), P('M13 13l3.4 3.4a1.5 1.5 0 0 0 2.1-2.1L13.7 9.2','b'), P('M8.5 8.5l3 3','c'), P('M5 6l-1-2M9 4l-.5-2M13 5l1-2','d')]),
  trend: svg([P('M3 17l6-6 4 4 8-8','a'), P('M17 7h4v4','b')]),
  arrow: svg([P('M5 12h14','a'), P('M13 6l6 6-6 6','b')]),
  chevR: svg([P('M9 6l6 6-6 6','a')]),
  spark: svg([P('M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2 2-6z','a')]),
  shield: svg([P('M12 3l8 3v5c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6l8-3z','a'), P('M9 12l2 2 4-4','b')]),
  alert: svg([P('M12 3l9 16H3l9-16z','a'), P('M12 10v4','b'), P('M12 17.5v.01','c')]),
  target: svg([C(12,12,9,'a'), C(12,12,5,'b'), C(12,12,1.4,'c')]),
  up: svg([P('M12 19V5','a'), P('M6 11l6-6 6 6','b')]),
  down: svg([P('M12 5v14','a'), P('M6 13l6 6 6-6','b')]),
  flat: svg([P('M5 12h14','a')]),
  fire: svg([P('M12 3c2 3 1 5 3 7 1.5 1.5 3 3 3 6a6 6 0 0 1-12 0c0-2 1-3.5 2-4.5C9 9 11 8 12 3z','a')]),
};

/* avatar — gradient monogram */
function Avatar({name, grad, size='m', style}){
  const initials = name.split(' ').map(w=>w[0]).slice(0,2).join('').toUpperCase();
  return h('div',{className:`av ${size}`, style:{background:`linear-gradient(135deg,${grad[0]},${grad[1]})`, ...style}}, initials);
}

/* tier hexagon with inner glyph */
function Hex({color, glyph, active, size=62}){
  const pts="50,4 91,27 91,73 50,96 9,73 9,27"; // hexagon viewBox 100
  const glow = active ? `drop-shadow(0 0 10px ${color}aa)` : 'none';
  return h('div',{className:'hexwrap', style:{width:size, height:size}},
    h('svg',{viewBox:'0 0 100 100', width:size, height:size, style:{filter:glow}},
      h('polygon',{points:pts, fill:active?color:'#fff', stroke:color, strokeWidth:active?0:5, strokeLinejoin:'round'}),
      !active && h('polygon',{points:pts, fill:'none', stroke:color, strokeWidth:5, strokeLinejoin:'round'}),
      h('g',{transform:'translate(50,50)', fill:active?'#fff':color, stroke:'none'}, glyph)
    )
  );
}
/* tier glyphs (centered at 0,0) */
const TierGlyph = {
  starter:  h('circle',{r:11, fill:'none', stroke:'currentColor', strokeWidth:5}),
  bronze:   h('rect',{x:-9,y:-9,width:18,height:18,rx:3}),
  silver:   h('polygon',{points:'0,-11 9.5,-5.5 9.5,5.5 0,11 -9.5,5.5 -9.5,-5.5'}),
  gold:     h('path',{d:'M0,-12 L3.5,-3.7 12,-3.7 5.2,1.4 7.6,9.7 0,4.7 -7.6,9.7 -5.2,1.4 -12,-3.7 -3.5,-3.7 Z'}),
  diamond: h('path',{d:'M-11,-9 L11,-9 L16,-2 L0,13 L-16,-2 Z'}),
  platinum: h('path',{d:'M-11,-4 L-5,3 0,-7 5,3 11,-4 9,9 -9,9 Z'}),
};
function tierHexInner(tierKey){ return TierGlyph[tierKey]; }

/* award ribbon badge — medallion + two solid fishtail ribbons (matches reference) */
function Ribbon({color, glyph, size=44}){
  const cx=24, cy=20;
  const glyphs = {
    star:    h('polygon',{points:'24,12 25.94,17.33 31.61,17.53 27.14,21.02 28.70,26.47 24,23.3 19.30,26.47 20.86,21.02 16.39,17.53 22.06,17.33', fill:color}),
    crown:   h('g',null,
      h('path',{d:'M16.4,24.6 L16.4,15 L20.2,18.6 L24,13 L27.8,18.6 L31.6,15 L31.6,24.6 Z', fill:color}),
      h('line',{x1:17.6, y1:22.2, x2:30.4, y2:22.2, stroke:'#fff', strokeWidth:1.3})),
    diamond: h('g',null,
      h('path',{d:'M18,14.6 L30,14.6 L33,19 L24,28 L15,19 Z', fill:color}),
      h('line',{x1:18, y1:18.4, x2:30, y2:18.4, stroke:'#fff', strokeWidth:1.1}),
      h('line',{x1:24, y1:18.4, x2:24, y2:28, stroke:'#fff', strokeWidth:1.1})),
  };
  return h('div',{className:'ribbon', style:{width:size, height:size*58/48}},
    h('svg',{viewBox:'0 0 48 58', width:size, height:size*58/48},
      // ribbon tails (behind medallion)
      h('polygon',{points:'21,31 15.5,32 9.5,51 13,47.5 16.8,52.5', fill:color}),
      h('polygon',{points:'27,31 32.5,32 38.5,51 35,47.5 31.2,52.5', fill:color}),
      h('line',{x1:18, y1:33, x2:12.5, y2:49.5, stroke:'#fff', strokeWidth:1, opacity:.45}),
      h('line',{x1:30, y1:33, x2:35.5, y2:49.5, stroke:'#fff', strokeWidth:1, opacity:.45}),
      // medallion
      h('circle',{cx:cx, cy:cy, r:14.5, fill:'#fff', stroke:color, strokeWidth:2.4}),
      glyphs[glyph] || glyphs.star
    )
  );
}

Object.assign(window, { h, Icons, Avatar, Hex, tierHexInner, TierGlyph, Ribbon });