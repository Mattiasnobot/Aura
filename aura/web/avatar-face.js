(() => {
'use strict';
/* ================= CANVAS ================= */
const canvas=document.getElementById('avatarCanvas');
const ctx=canvas.getContext('2d');
let W,H,S,CX,CY,DPR;
function resize(){
  DPR=Math.min(2,window.devicePixelRatio||1);
  const rect=canvas.getBoundingClientRect();
  const width=Math.max(1,rect.width||260),height=Math.max(1,rect.height||300);
  W=canvas.width=Math.floor(width*DPR);
  H=canvas.height=Math.floor(height*DPR);
  CX=W/2;CY=H*0.39;S=Math.min(W,H)*0.29;
}
addEventListener('resize',resize);resize();

/* ================= UTILS ================= */
const TAU=Math.PI*2;
const lerp=(a,b,t)=>a+(b-a)*t;
const clamp=(v,a,b)=>v<a?a:v>b?b:v;
function mulberry32(a){return function(){a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}
const rand=mulberry32(777);
function catmullClosed(pts,seg){
  const out=[],n=pts.length;
  for(let i=0;i<n;i++){
    const p0=pts[(i-1+n)%n],p1=pts[i],p2=pts[(i+1)%n],p3=pts[(i+2)%n];
    for(let j=0;j<seg;j++){
      const t=j/seg,t2=t*t,t3=t2*t;
      out.push([
        0.5*(2*p1[0]+(-p0[0]+p2[0])*t+(2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*t2+(-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3),
        0.5*(2*p1[1]+(-p0[1]+p2[1])*t+(2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*t2+(-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3)
      ]);
    }
  }
  return out;
}
function catmullOpen(pts,seg){
  const P=[pts[0],...pts,pts[pts.length-1]],out=[];
  for(let i=1;i<P.length-2;i++){
    const p0=P[i-1],p1=P[i],p2=P[i+1],p3=P[i+2];
    for(let j=0;j<seg;j++){
      const t=j/seg,t2=t*t,t3=t2*t;
      out.push([
        0.5*(2*p1[0]+(-p0[0]+p2[0])*t+(2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*t2+(-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3),
        0.5*(2*p1[1]+(-p0[1]+p2[1])*t+(2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*t2+(-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3)
      ]);
    }
  }
  out.push(pts[pts.length-1]);
  return out;
}

/* ================= FEMININE HEAD ================= */
/* The silhouette carries most of the read. Width is held high — at the temples
 * and cheekbones — and taken away below them, so the face tapers to a softer
 * chin instead of running straight down to a square jaw. */
const R_LAND=[
  [0,-1.30],[0.41,-1.26],[0.73,-1.08],[0.90,-0.78],[0.97,-0.42],
  [0.99,-0.02],[0.93,0.34],[0.78,0.66],[0.57,0.92],[0.33,1.07],
  [0.14,1.14],[0,1.17]
];
const loop=[...R_LAND];
for(let i=R_LAND.length-2;i>0;i--)loop.push([-R_LAND[i][0],R_LAND[i][1]]);
const SIL=catmullClosed(loop,12);
const LUT_Y0=-1.40,LUT_STEP=0.02,LUT_N=Math.ceil((1.25-LUT_Y0)/LUT_STEP)+1;
const LUTW=new Float32Array(LUT_N);
for(let i=0;i<LUT_N;i++){
  const y=LUT_Y0+i*LUT_STEP;let m=0;
  for(const p of SIL)if(Math.abs(p[1]-y)<0.05&&Math.abs(p[0])>m)m=Math.abs(p[0]);
  LUTW[i]=m;
}
function wAt(y){
  const f=clamp((y-LUT_Y0)/LUT_STEP,0,LUT_N-1.001),i=f|0,t=f-i;
  return LUTW[i]*(1-t)+LUTW[i+1]*t;
}
function faceDepth(x,y){
  const w=wAt(y);if(w<=0.01)return 0;
  const r=Math.min(1,Math.abs(x)/w),ax=Math.abs(x);
  let z=0.62*Math.pow(Math.max(0,1-r*r),0.62);
  z+=0.15*Math.exp(-((x/0.09)**2+((y-0.28)/0.14)**2));
  z+=0.06*Math.exp(-((x/0.36)**2+((y+0.38)/0.14)**2));
  z-=0.09*Math.exp(-(((ax-0.42)/0.15)**2+((y+0.08)/0.09)**2));
  z+=0.11*Math.exp(-((x/0.26)**2+((y-0.70)/0.15)**2));
  z+=0.08*Math.exp(-((x/0.14)**2+((y-1.02)/0.10)**2));
  z+=0.07*Math.exp(-(((ax-0.56)/0.20)**2+((y-0.35)/0.28)**2));
  return z;
}

/* ================= STATIC MESH ================= */
const SP=[],SL=[];
function sPoint(x,y,z,s){SP.push({x,y,z,s:s||1});return SP.length-1;}
const GLOW={sil:[],jaw:[],earL:[],earR:[],neckL:[],neckR:[],nose:[],browL:[],browR:[],hairRim:[],hairline:[],part:[]};
const NOST=[{x:0.075,y:0.345,r:-0.5},{x:-0.075,y:0.345,r:0.5}];
(function buildStatic(){
  let n=0,guard=0;
  while(n<2400&&guard<200000){guard++;
    const y=-1.28+rand()*2.42,w=wAt(y);if(w<0.04)continue;
    const x=(rand()*2-1)*w*0.97;
    sPoint(x,y,faceDepth(x,y)*(0.9+rand()*0.2),0.55+rand()*0.8);n++;
  }
  const hot=[[0,0.28,0.18],[0.42,-0.08,0.20],[-0.42,-0.08,0.20],[0,-0.42,0.28],
             [0,0.70,0.24],[0.42,-0.40,0.20],[-0.42,-0.40,0.20],[0,1.0,0.15]];
  n=0;guard=0;
  while(n<600&&guard<40000){guard++;
    const h=hot[(rand()*hot.length)|0];
    const x=h[0]+(rand()*2-1)*h[2],y=h[1]+(rand()*2-1)*h[2];
    const w=wAt(y);if(w<0.04||Math.abs(x)>w)continue;
    sPoint(x,y,faceDepth(x,y)*(0.92+rand()*0.16),0.5+rand()*0.6);n++;
  }
  const faceN=SP.length;
  n=0;guard=0;
  while(n<550&&guard<40000){guard++;
    const y=1.08+rand()*1.12;
    const wN=0.46+Math.max(0,y-1.60)*1.2;
    const x=(rand()*2-1)*wN*0.97;
    sPoint(x,y,0.30*Math.pow(Math.max(0,1-(x/wN)**2),0.7)-0.08,0.55+rand()*0.7);n++;
  }
  function linkRange(a,b,maxd){
    const M2=maxd*maxd,K=3;
    for(let i=a;i<b;i++){
      const best=[];
      for(let j=a;j<b;j++){if(i===j)continue;
        const dx=SP[i].x-SP[j].x,dy=SP[i].y-SP[j].y,d2=dx*dx+dy*dy;
        if(d2>M2)continue;
        best.push([d2,j]);
        if(best.length>K){let mi=0;for(let q=1;q<best.length;q++)if(best[q][0]>best[mi][0])mi=q;best.splice(mi,1);}
      }
      for(const q of best)if(q[1]>i)SL.push(i,q[1]);
    }
  }
  linkRange(0,faceN,0.14);
  linkRange(faceN,SP.length,0.15);
  for(let i=0;i<SIL.length;i++)GLOW.sil.push(sPoint(SIL[i][0],SIL[i][1],0.05,0.8));
  for(let i=0;i<=22;i++){
    const y=lerp(0.45,1.14,i/22),w=wAt(y),off=0.05*clamp(w/0.3,0,1);
    GLOW.jaw.push(sPoint(-(w-off),y,0.10,0.9));
  }
  for(let i=22;i>=0;i--){
    const y=lerp(0.45,1.14,i/22),w=wAt(y),off=0.05*clamp(w/0.3,0,1);
    GLOW.jaw.push(sPoint((w-off),y,0.10,0.9));
  }
  const nk=catmullOpen([[0.46,1.06],[0.50,1.50],[0.55,1.80],[0.85,2.10],[1.25,2.25]],6);
  for(const p of nk){GLOW.neckR.push(sPoint(p[0],p[1],0.02,0.8));GLOW.neckL.push(sPoint(-p[0],p[1],0.02,0.8));}
  const EAR=[[0.87,0.16],[0.91,-0.08],[0.99,-0.06],[1.04,0.12],[1.02,0.32],[0.94,0.42],[0.87,0.38]];
  for(const side of[-1,1]){
    const pts=catmullClosed(EAR,6).map(p=>[side*p[0],p[1]]);
    const store=side<0?GLOW.earL:GLOW.earR;
    for(const p of pts)store.push(sPoint(p[0],p[1],0.08,0.7));
    let prev=-1;
    for(let i=0;i<=8;i++){const a=Math.PI*0.6+Math.PI*1.4*i/8;
      const q=sPoint(side*(0.95+0.05*Math.cos(a)),0.16+0.12*Math.sin(a),0.10,0.7);
      if(prev>=0)SL.push(prev,q);prev=q;}
  }
  for(const side of[-1,1]){
    const store=side<0?GLOW.browL:GLOW.browR;
    let prevT=-1,prevB=-1;
    for(let i=0;i<=14;i++){const t=i/14;
      const x=side*lerp(0.16,0.76,t);
      /* Sat higher on the socket and arched more, and thinner with it: a low
       * straight brow is the single strongest masculine cue on this mesh. */
      const yT=-0.40-0.17*Math.sin(t*Math.PI*0.85)+0.07*t*t;
      const yB=yT+0.038-0.022*t;
      const pT=sPoint(x,yT,faceDepth(x,yT)+0.02,0.9);
      const pB=sPoint(x,yB,faceDepth(x,yB)+0.02,0.85);
      store.push(pT);
      if(prevT>=0){SL.push(prevT,pT);SL.push(prevB,pB);}
      SL.push(pT,pB);
      prevT=pT;prevB=pB;
      for(let yy=yT+0.016;yy<yB;yy+=0.016)sPoint(x,yy,faceDepth(x,yy)+0.02,0.6);
    }
  }
  /* nose: faint short bridge (no glow), glowing wings + tip */
  for(const side of[-1,1]){
    const br=catmullOpen([[side*0.050,-0.02],[side*0.058,0.06],[side*0.070,0.14],[side*0.090,0.21]],5);
    let prev=-1;
    for(const p of br){const q=sPoint(p[0],p[1],faceDepth(p[0],p[1])+0.02,0.6);if(prev>=0)SL.push(prev,q);prev=q;}
    const wg=catmullOpen([[side*0.09,0.21],[side*0.14,0.26],[side*0.15,0.325],[side*0.11,0.37],[side*0.075,0.37]],5);
    prev=-1;
    for(const p of wg){const q=sPoint(p[0],p[1],faceDepth(p[0],p[1])+0.03,0.85);GLOW.nose.push(q);if(prev>=0)SL.push(prev,q);prev=q;}
  }
  const tip=catmullOpen([[-0.05,0.30],[-0.04,0.345],[0,0.36],[0.04,0.345],[0.05,0.30]],5);
  {let prev=-1;for(const p of tip){const q=sPoint(p[0],p[1],faceDepth(p[0],p[1])+0.05,0.9);GLOW.nose.push(q);if(prev>=0)SL.push(prev,q);prev=q;}}
  for(const px of[-0.02,0.02]){let prev=-1;
    for(let i=0;i<=3;i++){const q=sPoint(px,0.44+0.03*i,faceDepth(px,0.47)+0.03,0.65);if(prev>=0)SL.push(prev,q);prev=q;}}
  for(const yy of[-0.62,-0.84,-1.04]){let prev=-1;
    for(let i=0;i<=12;i++){const t=i/12*2-1;
      const x=t*wAt(yy)*0.75,y=yy+0.07*(1-t*t);
      const q=sPoint(x,y,faceDepth(x,y)+0.01,0.55);if(prev>=0)SL.push(prev,q);prev=q;}}
  {let prev=-1;
   for(let i=0;i<=8;i++){const t=i/8*2-1;
     const q=sPoint(t*0.15,0.96+0.05*(1-t*t),faceDepth(t*0.15,0.98),0.7);if(prev>=0)SL.push(prev,q);prev=q;}}

  /* ================= HAIR ================= */
  const HR_LAND=[
    [0,-1.36],[0.50,-1.30],[0.85,-1.05],[1.05,-0.60],[1.14,-0.10],
    [1.16,0.40],[1.10,1.00],[1.02,1.60],[0.95,2.10],[0.80,2.30],
    [0.50,2.22],[0.22,2.34],[0,2.28]
  ];
  const hloop=[...HR_LAND];
  for(let i=HR_LAND.length-2;i>0;i--)hloop.push([-HR_LAND[i][0],HR_LAND[i][1]]);
  const HR=catmullClosed(hloop,10);
  for(const p of HR)GLOW.hairRim.push(sPoint(p[0],p[1],p[1]<-0.55?faceDepth(p[0],p[1])+0.05:-0.10,0.7));
  function hairW(y){
    if(y<-0.6)return wAt(clamp(y,-1.3,1.2))+0.08;
    if(y<0.4)return lerp(wAt(-0.6)+0.08,1.16,(y+0.6)/1.0);
    if(y<1.6)return lerp(1.16,1.02,(y-0.4)/1.2);
    return lerp(1.02,0.90,(y-1.6)/0.7);
  }
  function innerX(y){
    if(y<1.05)return lerp(0.06,wAt(clamp(y,-1.3,1.16))+0.02,clamp((y+1.30)/0.75,0,1));
    return lerp(0.48,0.70,clamp((y-1.05)/1.2,0,1));
  }
  const hl=catmullOpen([[-0.62,-0.55],[-0.40,-0.72],[0,-0.80],[0.40,-0.72],[0.62,-0.55]],6);
  for(const p of hl)GLOW.hairline.push(sPoint(p[0],p[1],faceDepth(p[0],p[1])+0.04,0.8));
  const pt=catmullOpen([[0,-1.30],[0.01,-1.05],[0,-0.80]],6);
  for(const p of pt)GLOW.part.push(sPoint(p[0],p[1],faceDepth(p[0],p[1])+0.06,0.8));
  for(const side of[-1,1]){
    const NS=16,grid=[];
    for(let sI=0;sI<NS;sI++){
      const t=(sI+0.5)/NS+(rand()-0.5)*0.04;
      const ph=rand()*TAU,fq=1.5+rand()*1.5,amp=0.02+rand()*0.04;
      const y0=-1.26+t*0.35+rand()*0.08;
      const idxs=[];let prev=-1;
      const nn=24;
      for(let i=0;i<=nn;i++){
        const u=i/nn,y=lerp(y0,2.26,u);
        const x=side*(lerp(innerX(y)*0.99,hairW(y),t)+Math.sin(u*fq*TAU+ph)*amp*u);
        const z=y<-0.55?faceDepth(x,y)+0.05:lerp(0.05,-0.18,t);
        const q=sPoint(x,y,z,0.5+rand()*0.35);
        idxs.push(q);if(prev>=0)SL.push(prev,q);prev=q;
      }
      grid.push(idxs);
    }
    for(let sI=0;sI<NS-1;sI++)for(let i=0;i<=24;i+=3)SL.push(grid[sI][i],grid[sI+1][i]);
  }
})();

/* ================= STARS + SPRITE ================= */
const STARS=[];
for(let i=0;i<110;i++)STARS.push({x:rand(),y:rand(),p:rand()*TAU,s:0.4+rand()*1.1});
const sprite=document.createElement('canvas');sprite.width=sprite.height=64;
{const g=sprite.getContext('2d');
 const grd=g.createRadialGradient(32,32,0,32,32,32);
 grd.addColorStop(0,'rgba(255,255,255,1)');grd.addColorStop(0.25,'rgba(160,220,255,0.9)');
 grd.addColorStop(0.6,'rgba(70,160,255,0.28)');grd.addColorStop(1,'rgba(70,160,255,0)');
 g.fillStyle=grd;g.fillRect(0,0,64,64);}

/* ================= STATE / UPDATE ================= */
const PALETTES={
 idle:[89,229,243],listening:[98,184,255],thinking:[188,126,239],
 working:[255,180,84],success:[105,217,138],error:[255,119,119]
};
const preferences={motion:'natural',intensity:65,quality:'auto',
 reduced:matchMedia('(prefers-reduced-motion: reduce)').matches};
const state={yaw:0,pitch:0,roll:0,mx:0,my:0,eyeX:0,eyeY:0,
 blink:0,blinkStart:-1,blinkDuration:190,blinkRepeats:0,nextBlink:performance.now()+3200,
 mouth:0,mouthShape:'neutral',talking:false,syl:0,nextSyl:0,
 speechCues:[],speechStarted:0,speechDuration:0,cueIndex:0,speechSource:'fallback',
 nodT:99,shakeT:99,nextIdle:performance.now()+12000,mode:'idle',smile:0,brow:0,squint:0,
 color:[...PALETTES.idle],targetColor:[...PALETTES.idle]};
function tone(alpha,light=0){
  const c=state.color.map(value=>Math.round(lerp(value,255,light)));
  return `rgba(${c[0]},${c[1]},${c[2]},${alpha})`;
}
function motionStrength(){
  const mode={calm:0.55,natural:1,expressive:1.28}[preferences.motion]||1;
  return (preferences.reduced?0.14:mode)*clamp(preferences.intensity/65,0,1.45);
}
function scheduleBlink(now,soon=false){
  const modeFactor=state.mode==='thinking'?0.82:state.mode==='listening'?1.15:1;
  state.nextBlink=now+(soon?320:2800+Math.random()*4400)*modeFactor;
}
function triggerBlink(doubleBlink=false){
  state.blinkStart=performance.now();state.blinkDuration=155+Math.random()*70;
  state.blinkRepeats=doubleBlink?1:0;
}
function expressionTargets(){
  if(state.mode==='success')return[0.78,0.16,0];
  if(state.mode==='error')return[-0.55,-0.2,0.2];
  if(state.mode==='listening')return[0.16,0.3,-0.05];
  if(state.mode==='thinking')return[0.02,0.13,0.08];
  if(state.mode==='working')return[0.08,-0.04,0.05];
  return[0.1,0,0];
}
function update(now,dt){
  const strength=motionStrength();
  let gy=0,gp=0;
  if(state.nodT<2.2)gp+=Math.exp(-state.nodT*2.2)*Math.sin(state.nodT*8)*0.13*strength;
  if(state.shakeT<2.0)gy+=Math.exp(-state.shakeT*2.4)*Math.sin(state.shakeT*8)*0.16*strength;
  state.nodT+=dt;state.shakeT+=dt;
  if(state.talking){gp+=(Math.sin(now*0.0042)*0.012+state.mouth*0.016)*strength;gy+=Math.sin(now*0.0027)*0.018*strength;}
  gy+=Math.sin(now*0.00032)*0.021*strength;gp+=Math.sin(now*0.00043+2)*0.014*strength;
  if(state.mode==='thinking')gy+=Math.sin(now*0.0011)*0.018*strength;
  if(state.mode==='working')gp+=Math.sin(now*0.0015)*0.01*strength;
  if(now>state.nextIdle){state.nextIdle=now+7000+Math.random()*8000;
    if(!preferences.reduced)state.nodT=0;}
  state.eyeX+=(state.mx-state.eyeX)*Math.min(1,dt*10);
  state.eyeY+=(state.my-state.eyeY)*Math.min(1,dt*10);
  state.yaw+=((state.mx*0.16*strength+gy)-state.yaw)*Math.min(1,dt*3.7);
  state.pitch+=((state.my*0.105*strength+gp)-state.pitch)*Math.min(1,dt*3.7);
  state.roll+=((state.mx*0.025*strength)-state.roll)*Math.min(1,dt*3);
  if(now>state.nextBlink&&state.blinkStart<0){triggerBlink(Math.random()<0.12);scheduleBlink(now);}
  if(state.blinkStart>=0&&now>=state.blinkStart){
    const p=(now-state.blinkStart)/state.blinkDuration;
    if(p>=1){
      state.blink=0;
      if(state.blinkRepeats>0){state.blinkRepeats--;state.blinkStart=now+90;}
      else state.blinkStart=-1;
    }else{
      const shaped=p<0.42?p/0.42:(1-p)/0.58;
      state.blink=Math.sin(clamp(shaped,0,1)*Math.PI/2);
    }
  }
  let target=0;
  if(state.talking){
    const elapsed=now-state.speechStarted;
    if(state.speechCues.length&&elapsed<=state.speechDuration+180){
      while(state.cueIndex+1<state.speechCues.length&&state.speechCues[state.cueIndex+1].at_ms<=elapsed)state.cueIndex++;
      const cue=state.speechCues[state.cueIndex];
      const next=state.speechCues[state.cueIndex+1];
      target=clamp(Number(cue?.open)||0,0,1);
      // Cues are sampled every 55 ms, well below frame rate. Reading between two
      // of them keeps the jaw on the syllable instead of stepping after it.
      if(next){
        const span=Math.max(1,next.at_ms-cue.at_ms);
        const ratio=clamp((elapsed-cue.at_ms)/span,0,1);
        target=clamp(target+(clamp(Number(next.open)||0,0,1)-target)*ratio,0,1);
      }
      state.mouthShape=cue?.shape||'audio';
    }else{
      if(now>state.nextSyl){state.syl=Math.random();state.nextSyl=now+100+Math.random()*150;}
      const wob=Math.abs(Math.sin(now*0.011)+0.45*Math.sin(now*0.019+1.3))/1.45;
      target=clamp(0.08+0.72*state.syl*wob,0,1);state.mouthShape='neutral';
    }
  }
  state.mouth+=(target-state.mouth)*Math.min(1,dt*(target>state.mouth?22:13));
  if(!state.talking&&state.mouth<0.02)state.mouth=0;
  const [smile,brow,squint]=expressionTargets();
  state.smile+=(smile-state.smile)*Math.min(1,dt*5);
  state.brow+=(brow-state.brow)*Math.min(1,dt*5);
  state.squint+=(squint-state.squint)*Math.min(1,dt*5);
  for(let i=0;i<3;i++)state.color[i]+=((state.targetColor[i]||0)-state.color[i])*Math.min(1,dt*3.5);
}

/* ================= PROJECTION / HELPERS ================= */
let cyaw=1,syaw=0,cpit=1,spit=0,crol=1,srol=0;
function project(x,y,z){
  let X=x*cyaw+z*syaw,Z=z*cyaw-x*syaw;
  let Y=y*cpit-Z*spit;Z=y*spit+Z*cpit;
  const X2=X*crol-Y*srol,Y2=X*srol+Y*crol;
  const p=3.1/(3.1-Z);
  return[CX+X2*S*p,CY+Y2*S*p,S*p,Z];
}
function strokePts(pts,close){ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));if(close)ctx.closePath();ctx.stroke();}
function glowPts(pts,width,color,blur,close){
  ctx.save();ctx.shadowColor=tone(0.9);ctx.shadowBlur=blur*DPR;
  ctx.strokeStyle=color;ctx.lineWidth=width*DPR;ctx.lineCap='round';
  strokePts(pts,close);ctx.restore();
}
function glowIdx(idx,width,color,blur,close){glowPts(idx.map(i=>[proj[i*3],proj[i*3+1]]),width,color,blur,close);}
function glowDot(x,y,s,a){const prev=ctx.globalCompositeOperation;ctx.globalCompositeOperation='lighter';
  ctx.globalAlpha=a;const sz=s*S*0.008;ctx.drawImage(sprite,x-sz,y-sz,sz*2,sz*2);
  ctx.globalAlpha=1;ctx.globalCompositeOperation=prev;}

/* ================= EYES ================= */
function drawEyes(){
  const b=clamp(state.blink+state.squint*0.34,0,0.94);
  for(const side of[-1,1]){
    const cx=side*0.42,cyy=-0.08,hw=0.185,z0=faceDepth(cx,cyy)+0.02;
    const u=lerp(0.12,0.005,b),l=lerp(0.075,-0.005,b);
    const up=[],lo=[];
    for(let i=0;i<=14;i++){const t=i/14*2-1,shp=1-t*t,x=cx+t*hw,z=faceDepth(x,cyy)+0.02;
      up.push(project(x,cyy-u*shp,z));lo.push(project(x,cyy+l*shp,z));}
    const open=1-b;
    if(open>0.15){
      const irisX=cx+state.eyeX*0.043,irisY=cyy+state.eyeY*0.026;
      const c=project(irisX,irisY,z0),r=0.082*c[2];
      ctx.save();ctx.translate(c[0],c[1]);ctx.scale(1,open);
      ctx.globalCompositeOperation='lighter';
      const g=ctx.createRadialGradient(0,0,0,0,0,r*1.6);
      g.addColorStop(0,tone(0.35));g.addColorStop(1,tone(0));
      ctx.fillStyle=g;ctx.beginPath();ctx.arc(0,0,r*1.6,0,TAU);ctx.fill();
      ctx.globalCompositeOperation='source-over';
      const ig=ctx.createRadialGradient(0,0,r*0.1,0,0,r);
      ig.addColorStop(0,tone(1,0.75));ig.addColorStop(0.35,tone(0.92));
      ig.addColorStop(0.8,'#0a3358');ig.addColorStop(1,'#03101e');
      ctx.fillStyle=ig;ctx.beginPath();ctx.arc(0,0,r,0,TAU);ctx.fill();
      ctx.fillStyle='#010a14';ctx.beginPath();ctx.arc(0,0,r*0.42,0,TAU);ctx.fill();
      ctx.fillStyle='rgba(255,255,255,0.85)';ctx.beginPath();ctx.arc(-r*0.28,-r*0.30,r*0.11,0,TAU);ctx.fill();
      ctx.strokeStyle=tone(0.9,0.55);ctx.lineWidth=1.1*DPR;
      ctx.beginPath();ctx.arc(0,0,r,0,TAU);ctx.stroke();
      ctx.restore();
    }
    glowPts(up,1.4,tone(0.95,0.55),4);
    glowPts(lo,1.0,tone(0.75,0.34),3);
    for(let L=0;L<3;L++){
      const tt=0.70+L*0.14;
      const bx=cx+side*hw*tt,by=cyy-u*(1-tt*tt);
      const fl=[project(bx,by,z0),
                project(bx+side*0.03*(1+L*0.35),by-0.030-0.012*L,z0),
                project(bx+side*0.05*(1+L*0.35),by-0.050-0.018*L,z0)];
      glowPts(fl,0.9,tone(0.85,0.62),2);
    }
    const cr=[];
    for(let i=0;i<=10;i++){const t=i/10*2-1,x=cx+t*hw*0.9;
      const asym=state.mode==='thinking'&&side===1?0.016:0;
      cr.push(project(x,cyy-0.16*(1-0.7*t*t)-state.brow*0.026+0.02*b-asym,faceDepth(x,cyy)+0.01));}
    ctx.strokeStyle=tone(0.45);ctx.lineWidth=DPR;strokePts(cr);
    for(let i=0;i<=14;i+=2){glowDot(up[i][0],up[i][1],0.9,0.9);glowDot(lo[i][0],lo[i][1],0.85,0.8);}
  }
}

/* ================= MOUTH (fixed typo) ================= */
function drawMouth(){
  const m=state.mouth,my=0.70;
  const shapeWidth=state.mouthShape==='round'?0.82:state.mouthShape==='wide'?1.09:1;
  const hw=0.30*(1-0.17*m)*shapeWidth;
  const upT=[],seU=[],seL=[],loB=[],loC=[];
  for(let i=0;i<=16;i++){
    const t=i/16*2-1,shp=Math.max(0,1-t*t),at=Math.abs(t);
    /* A deeper cupid's bow and a fuller upper lip. */
    const bow=-0.021*Math.exp(-(((at-0.33)/0.17)**2))+0.015*Math.exp(-((t/0.11)**2));
    const x=t*hw,z=faceDepth(x,my)+0.03;
    const expression=-state.smile*0.036*Math.pow(Math.abs(t),1.6);
    upT.push(project(x,my-0.062*shp+bow*shp+0.003+expression,z));
    const yU=my+0.010*shp-0.026*t*t-0.020*m*shp+expression;
    seU.push(project(x,yU,z));
    const open=m*0.17*Math.pow(shp,0.85);
    seL.push(project(x,yU+open,z));
    loB.push(project(x,yU+open+(0.095+0.02*m)*Math.pow(shp,1.1)+0.004,z));
    loC.push(project(x,yU+open+(0.12+0.02*m)*shp+0.006,z-0.02));
  }
  if(m>0.04){
    ctx.beginPath();
    seU.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));
    for(let i=seL.length-1;i>=0;i--)ctx.lineTo(seL[i][0],seL[i][1]);
    ctx.closePath();ctx.fillStyle='rgba(3,14,26,0.94)';ctx.fill();
    if(m>0.22){
      ctx.strokeStyle=tone(0.55*m,0.65);ctx.lineWidth=DPR;
      ctx.beginPath();
      for(let i=3;i<=13;i++){const y=seU[i][1]+(seL[i][1]-seU[i][1])*0.35;
        i===3?ctx.moveTo(seU[i][0],y):ctx.lineTo(seU[i][0],y);}
      ctx.stroke();
    }
  }
  glowPts(upT,1.2,tone(0.9,0.5),4);
  ctx.strokeStyle='rgba(4,24,44,0.9)';ctx.lineWidth=2.0*DPR;strokePts(seU);
  glowPts(seU,0.8,tone(0.65,0.35),2);
  if(m>0.04)glowPts(seL,0.8,tone(0.65,0.35),2);
  glowPts(loB,1.4,tone(0.95,0.5),5);
  ctx.strokeStyle=tone(0.35);ctx.lineWidth=DPR;strokePts(loC);
  for(let i=0;i<=16;i+=2){glowDot(upT[i][0],upT[i][1],0.9,0.9);glowDot(loB[i][0],loB[i][1],1.0,0.9);}
}

/* ================= RENDER ================= */
const proj=new Float32Array(SP.length*3);
const NB=4,ALPHA=[0.12,0.20,0.30,0.42],segs=[[],[],[],[]];
function render(now,stride=1){
  ctx.clearRect(0,0,W,H);
  ctx.globalCompositeOperation='lighter';
  for(let starIndex=0;starIndex<STARS.length;starIndex+=stride){const st=STARS[starIndex];
    ctx.globalAlpha=0.2+0.5*(0.5+0.5*Math.sin(now*0.001+st.p*7));
    const sz=st.s*DPR*1.4;ctx.drawImage(sprite,st.x*W-sz,st.y*H-sz,sz*2,sz*2);
  }
  ctx.globalAlpha=1;ctx.globalCompositeOperation='source-over';
  cyaw=Math.cos(state.yaw);syaw=Math.sin(state.yaw);
  cpit=Math.cos(state.pitch);spit=Math.sin(state.pitch);
  crol=Math.cos(state.roll);srol=Math.sin(state.roll);
  for(let i=0;i<SP.length;i++){
    const q=project(SP[i].x,SP[i].y,SP[i].z);
    proj[i*3]=q[0];proj[i*3+1]=q[1];proj[i*3+2]=q[3];
  }
  for(let s=0;s<NB;s++)segs[s].length=0;
  for(let i=0;i<SL.length;i+=2*stride){
    const a=SL[i],b=SL[i+1],z=(proj[a*3+2]+proj[b*3+2])/2;
    const bkt=Math.min(NB-1,(clamp((z+0.4)/1.0,0,1)*NB)|0);
    segs[bkt].push(proj[a*3],proj[a*3+1],proj[b*3],proj[b*3+1]);
  }
  ctx.lineCap='round';ctx.lineWidth=0.8*DPR;
  for(let s=0;s<NB;s++){
    const arr=segs[s];if(!arr.length)continue;
    ctx.strokeStyle=tone(ALPHA[s]);
    ctx.beginPath();
    for(let k=0;k<arr.length;k+=4){ctx.moveTo(arr[k],arr[k+1]);ctx.lineTo(arr[k+2],arr[k+3]);}
    ctx.stroke();
  }
  ctx.globalCompositeOperation='lighter';
  for(let i=0;i<SP.length;i+=stride){
    ctx.globalAlpha=Math.min(1,0.55+0.55*clamp((proj[i*3+2]+0.4)/1.0,0,1));
    const sz=SP[i].s*S*0.008;
    ctx.drawImage(sprite,proj[i*3]-sz,proj[i*3+1]-sz,sz*2,sz*2);
  }
  ctx.globalAlpha=1;ctx.globalCompositeOperation='source-over';
  glowIdx(GLOW.hairRim,1.2,tone(0.62,0.22),8,true);
  glowIdx(GLOW.hairline,1.0,tone(0.7,0.35),4);
  glowIdx(GLOW.part,1.0,tone(0.62,0.35),3);
  glowIdx(GLOW.sil,1.3,tone(0.82,0.42),8,true);
  glowIdx(GLOW.jaw,1.5,tone(0.88,0.54),10);
  glowIdx(GLOW.neckL,1.2,tone(0.72,0.32),6);
  glowIdx(GLOW.neckR,1.2,tone(0.72,0.32),6);
  glowIdx(GLOW.earL,1.0,tone(0.66,0.32),5,true);
  glowIdx(GLOW.earR,1.0,tone(0.66,0.32),5,true);
  glowIdx(GLOW.nose,1.1,tone(0.82,0.5),5);
  glowIdx(GLOW.browL,1.1,tone(0.82,0.44),4);
  glowIdx(GLOW.browR,1.1,tone(0.82,0.44),4);
  for(const n of NOST){
    const c=project(n.x,n.y,faceDepth(n.x,n.y)+0.04);
    ctx.save();ctx.translate(c[0],c[1]);ctx.rotate(n.r+state.yaw*0.6);
    ctx.fillStyle='rgba(2,10,20,0.9)';
    ctx.beginPath();ctx.ellipse(0,0,0.030*c[2],0.016*c[2],0,0,TAU);ctx.fill();
    ctx.strokeStyle=tone(0.58,0.38);ctx.lineWidth=DPR;
    ctx.beginPath();ctx.ellipse(0,0,0.030*c[2],0.016*c[2],0,0,TAU);ctx.stroke();
    ctx.restore();
  }
  drawMouth();
  drawEyes();
}

/* ================= AURA INTEGRATION / LOOP ================= */
let stopped=false,animationFrame=0,last=performance.now(),lastDraw=0,resizeObserver=null;
let intersectionObserver=null,inViewport=true,pageVisible=!document.hidden,adaptiveStride=1,renderCost=8;
function renderProfile(){
  if(preferences.reduced)return{fps:15,stride:2};
  if(preferences.quality==='low')return{fps:24,stride:2};
  if(preferences.quality==='high')return{fps:60,stride:1};
  return{fps:renderCost>15?30:45,stride:adaptiveStride};
}
function frame(now){
  if(stopped)return;
  animationFrame=requestAnimationFrame(frame);
  if(!pageVisible||!inViewport){last=now;return;}
  const profile=renderProfile();
  if(now-lastDraw<1000/profile.fps)return;
  const dt=Math.min(0.05,(now-last)/1000);last=now;lastDraw=now;
  const started=performance.now();
  try{update(now,dt);render(now,profile.stride);}catch(err){console.error('Aura face frame error:',err);}
  const cost=performance.now()-started;
  renderCost=renderCost*0.92+cost*0.08;
  if(preferences.quality==='auto')adaptiveStride=renderCost>18?2:renderCost<11?1:adaptiveStride;
  canvas.dataset.fps=String(profile.fps);canvas.dataset.detail=profile.stride===1?'full':'reduced';
}

function handleVisibility(){pageVisible=!document.hidden;canvas.dataset.visible=String(pageVisible&&inViewport);}
document.addEventListener('visibilitychange',handleVisibility);
const reducedQuery=matchMedia('(prefers-reduced-motion: reduce)');
const handleReduced=event=>{preferences.reduced=event.matches;canvas.dataset.reducedMotion=String(event.matches);};
reducedQuery.addEventListener?.('change',handleReduced);

class AuraAvatar3D{
  constructor(target,root){
    if(target!==canvas)throw new Error('Aura face canvas mismatch.');
    this.root=root;
    stopped=false;
    canvas.dataset.renderer='canvas-depth-projection';
    canvas.dataset.model='feminine-digital-human';
    canvas.dataset.meshVertices=String(SP.length);
    canvas.dataset.meshEdges=String(SL.length/2);
    canvas.dataset.reducedMotion=String(preferences.reduced);
    resizeObserver=window.ResizeObserver?new ResizeObserver(resize):null;
    resizeObserver?.observe(canvas);
    intersectionObserver=window.IntersectionObserver?new IntersectionObserver(entries=>{
      inViewport=Boolean(entries[0]?.isIntersecting);handleVisibility();
    },{threshold:0.02}):null;
    intersectionObserver?.observe(canvas);
    resize();
    if(!animationFrame)animationFrame=requestAnimationFrame(frame);
  }
  setGaze(x,y){
    state.mx=clamp(Number(x)||0,-1,1);
    state.my=clamp(Number(y)||0,-1,1);
  }
  setSpeaking(active){
    state.talking=Boolean(active);
    if(state.talking&&!state.speechStarted)state.speechStarted=performance.now();
    if(!state.talking){state.speechCues=[];state.speechStarted=0;state.cueIndex=0;}
    this.root?.classList.toggle('speaking',state.talking);
  }
  setSpeechCues(cues,durationMs,source='timing',alreadyElapsedMs=0){
    state.speechCues=(Array.isArray(cues)?cues:[]).slice(0,4000).map(cue=>({
      at_ms:Math.max(0,Number(cue.at_ms)||0),open:clamp(Number(cue.open)||0,0,1),
      shape:String(cue.shape||'neutral')
    }));
    state.speechDuration=Math.max(0,Number(durationMs)||0);
    state.speechSource=String(source||'timing');
    // Playback began on the Python side when these cues were pushed; they only
    // reach the page a poll later. Starting the clock at zero here is what put
    // the mouth behind the voice by that whole delay.
    const elapsed=clamp(Number(alreadyElapsedMs)||0,0,600);
    state.speechStarted=performance.now()-elapsed;state.cueIndex=0;
    canvas.dataset.speechSource=state.speechSource;
  }
  applySettings(values={}){
    const motion=String(values.motion||preferences.motion);
    const quality=String(values.quality||preferences.quality);
    preferences.motion=['calm','natural','expressive'].includes(motion)?motion:'natural';
    preferences.quality=['auto','high','low'].includes(quality)?quality:'auto';
    preferences.intensity=clamp(Number(values.intensity??preferences.intensity)||0,0,100);
    canvas.dataset.motion=preferences.motion;canvas.dataset.quality=preferences.quality;
    canvas.dataset.intensity=String(preferences.intensity);
  }
  setState(name){
    const next=Object.prototype.hasOwnProperty.call(PALETTES,name)?name:'idle';
    if(next===state.mode)return;
    state.mode=next;state.targetColor=[...PALETTES[next]];canvas.dataset.state=next;
    if(next==='success'){state.nodT=0;triggerBlink();}
    else if(next==='error')state.shakeT=0;
    else if(next==='listening'){triggerBlink();scheduleBlink(performance.now());}
  }
  pulse(){triggerBlink();state.nodT=0;}
  destroy(){
    stopped=true;
    cancelAnimationFrame(animationFrame);
    animationFrame=0;
    resizeObserver?.disconnect();
    intersectionObserver?.disconnect();
    document.removeEventListener('visibilitychange',handleVisibility);
    reducedQuery.removeEventListener?.('change',handleReduced);
  }
}

window.AuraAvatar3D=AuraAvatar3D;
})();
