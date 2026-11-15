/* DynaCook — « Surprends-moi » : invente une recette avec ce qu'il y a dans le frigo, le placard et le congélo.
   Pas d'IA en ligne : des modèles de plats qui se remplissent avec vos produits, en priorité ceux qui périment bientôt. */
(function(){
const ART={"poulet":"le ","bœuf haché":"le ","bœuf":"le ","porc":"le ","canard":"le ","saumon":"le ","cabillaud":"le ","thon":"le ","jambon":"le ","chorizo":"le ","riz":"le ","lait":"le ","beurre":"le ","fromage râpé":"le ","parmesan":"le ","reblochon":"le ","chèvre":"le ","comté":"le ","fromage":"le ","potiron":"le ","brocoli":"le ","chou-fleur":"le ","poivron":"le ","concombre":"le ","maïs":"le ","poireau":"le ","lait de coco":"le ","pesto":"le ","pain":"le ","pain de mie":"le ","cordon bleu":"le ","chocolat":"le ","sucre":"le ","persil":"le ","basilic":"le ","citron":"le ","yaourt":"le ","miel":"le ","gingembre":"le ",
"crème fraîche":"la ","courgette":"la ","tomate":"la ","carotte":"la ","aubergine":"la ","salade":"la ","pomme de terre":"la ","sauce tomate":"la ","mozzarella":"la ","feta":"la ","farine":"la ","banane":"la ","poire":"la ","pomme":"la ","pâte brisée":"la ","pâte feuilletée":"la ","pâte à pizza":"la ","semoule":"la ","endive":"l'","échalote":"l'","coriandre":"la ","ciboulette":"la ",
"oignon":"l'","ail":"l'","avocat":"l'","œufs":"les ","pâtes":"les ","nouilles":"les ","gnocchis":"les ","lardons":"les ","saucisses":"les ","crevettes":"les ","champignons":"les ","épinards":"les ","haricots verts":"les ","petits pois":"les ","pois chiches":"les ","lentilles":"les ","haricots rouges":"les ","tortillas":"les ","galettes de sarrasin":"les ","légumes surgelés":"les ","fraises":"les ","lasagnes":"les "};
const PLURAL_SHOW={"tomate":1,"carotte":1,"courgette":1,"pomme de terre":1,"poivron":1,"endive":1,"poireau":1,"pomme":1,"poire":1,"banane":1,"aubergine":1};
const art=k=>ART[k]||"";
const the=(k,pl)=>{const a=art(k);return(pl&&PLURAL_SHOW[k]?"les ":a)+(pl&&PLURAL_SHOW[k]?plur(k):k)};
const au=(k,pl)=>{const a=pl&&PLURAL_SHOW[k]?"les ":art(k);const n=pl&&PLURAL_SHOW[k]?plur(k):k;return(a==="le "?"au ":a==="la "?"à la ":a==="les "?"aux ":a==="l'"?"à l'":"au ")+n};
const de=k=>(/^[aeiouyhœéèêà]/i.test(k)?"d'":"de ")+k;
const plur=k=>/[sx]$/.test(k)?k:k.replace(/^(\S+)/,"$1s").replace("poireaus","poireaux");
const joinFr=a=>a.length<2?(a[0]||""):a.slice(0,-1).join(", ")+" et "+a[a.length-1];
const AVEC={"riz":"avec du riz","pâtes":"avec des pâtes","semoule":"avec de la semoule","pomme de terre":"avec des pommes de terre sautées","nouilles":"avec des nouilles"};
const capf=s=>s?s[0].toUpperCase()+s.slice(1):s;
const QTY={"pâtes":"200 g","riz":"150 g","nouilles":"200 g","gnocchis":"500 g","pomme de terre":"600 g","semoule":"150 g","poulet":"300 g","bœuf haché":"250 g","bœuf":"300 g","porc":"300 g","canard":"300 g","saumon":"250 g","cabillaud":"250 g","crevettes":"200 g","thon":"150 g","lardons":"150 g","jambon":"3 tranches","chorizo":"60 g","saucisses":"4","cordon bleu":"2","œufs":"4","courgette":"1","tomate":"2","carotte":"2","aubergine":"1","poivron":"1","champignons":"200 g","brocoli":"300 g","chou-fleur":"300 g","épinards":"150 g","haricots verts":"200 g","poireau":"1","potiron":"600 g","endive":"4","petits pois":"150 g","maïs":"100 g","légumes surgelés":"300 g","oignon":"1","échalote":"1","ail":"1 gousse","crème fraîche":"20 cl","lait":"30 cl","fromage râpé":"60 g","mozzarella":"1","parmesan":"40 g","chèvre":"100 g","feta":"100 g","comté":"60 g","fromage":"80 g","reblochon":"½","sauce tomate":"300 g","lait de coco":"40 cl","pesto":"3 cs","pâte brisée":"1 rouleau","pâte feuilletée":"1 rouleau","pâte à pizza":"1 rouleau","tortillas":"4","pain de mie":"4 tranches","pain":"4 tranches","galettes de sarrasin":"2","salade":"1","concombre":"1","avocat":"1","farine":"100 g","sucre":"60 g","beurre":"60 g","chocolat":"150 g","pomme":"4","poire":"4","banane":"2","fraises":"250 g","pois chiches":"250 g","lentilles":"150 g","haricots rouges":"250 g","sauce soja":"3 cs","persil":"1 poignée","basilic":"1 poignée","citron":"1","fromage blanc":"3 cs","yaourt":"1"};
function line(k){const q=QTY[k]||"1";const m=q.match(/^([\d½,.]+)\s*(.*)$/);if(!m)return q+" "+k;if(m[2])return m[1]+" "+m[2]+" "+de(k);return m[1]+" "+(+m[1]>1||m[1]==="½"?(m[1]==="½"?k:plur(k)):k)}
const G={
 pasta:["pâtes","nouilles","gnocchis"],
 starch:["riz","pâtes","pomme de terre","semoule","nouilles"],
 meat:["poulet","bœuf haché","bœuf","porc","canard","saucisses","lardons","chorizo"],
 fish:["saumon","cabillaud","crevettes"],
 cold:["thon","jambon"],
 legum:["pois chiches","lentilles","haricots rouges"],
 veg:["courgette","champignons","poivron","tomate","carotte","brocoli","épinards","haricots verts","poireau","aubergine","chou-fleur","potiron","petits pois","maïs","légumes surgelés","endive"],
 soup:["carotte","courgette","poireau","potiron","pomme de terre","brocoli","chou-fleur","tomate","épinards","petits pois","champignons"],
 cheese:["fromage râpé","mozzarella","parmesan","chèvre","feta","comté","fromage","reblochon"],
 aroma:["oignon","échalote","ail"],
 herb:["persil","basilic","coriandre","ciboulette"],
 fruit:["pomme","poire","banane","fraises"]
};
const VT={courgette:6,champignons:6,poivron:8,tomate:4,carotte:10,brocoli:6,"épinards":2,"haricots verts":8,poireau:8,aubergine:10,"chou-fleur":8,potiron:12,"petits pois":5,"maïs":2,"légumes surgelés":8,endive:8};
const NOCUT={"petits pois":1,"maïs":1,"légumes surgelés":1,"épinards":1};
const PREP={"poulet":"Coupe le poulet en morceaux et fais-le dorer 8 min dans un filet d'huile.","bœuf haché":"Fais cuire le bœuf haché 5 min dans un filet d'huile en l'émiettant.","bœuf":"Coupe le bœuf en fines lanières et saisis-le 3 min à feu vif.","porc":"Coupe le porc en morceaux et fais-le dorer 8 min dans un filet d'huile.","canard":"Coupe le canard en lanières et fais-le dorer 6 min.","saumon":"Coupe le saumon en cubes et fais-le cuire 4 min à feu moyen.","cabillaud":"Fais cuire le cabillaud 4 min de chaque côté, puis émiette-le.","crevettes":"Fais sauter les crevettes 3 min dans un filet d'huile.","thon":"Égoutte et émiette le thon.","lardons":"Fais dorer les lardons 5 min sans matière grasse.","jambon":"Coupe le jambon en lanières.","chorizo":"Coupe le chorizo en rondelles et fais-le revenir 2 min.","saucisses":"Fais dorer les saucisses 10 min, puis coupe-les en rondelles.","pois chiches":"Égoutte et rince les pois chiches.","lentilles":"Fais cuire les lentilles 20 min dans l'eau, puis égoutte-les.","haricots rouges":"Égoutte et rince les haricots rouges."};
const COOK={"pâtes":"Fais cuire les pâtes dans une grande casserole d'eau salée, 10 min.","nouilles":"Fais cuire les nouilles 4 min dans l'eau bouillante, puis égoutte-les.","riz":"Fais cuire le riz dans l'eau salée, 12 min.","semoule":"Verse la semoule dans un saladier avec le même volume d'eau chaude salée, couvre 5 min puis égrène à la fourchette.","pomme de terre":"Coupe les pommes de terre en petits cubes et fais-les dorer 15 min dans un filet d'huile.","gnocchis":"Fais dorer les gnocchis 8 min dans un filet d'huile, sans les faire bouillir."};

function rnd(a){return a[Math.floor(Math.random()*a.length)]}
function makeInv(items){
  const inv={};
  items.forEach(x=>{const k=x.k;if(!k)return;const d=x.d==null?60:x.d;if(!inv[k]||d<inv[k].d)inv[k]={k,d,name:x.n}});
  return inv;
}
function urg(d){return d<0?2:d<=1?6:d<=3?3.5:d<=7?1.6:.6}
/* choisit dans une liste : d'abord ce qui périme, avec un peu de hasard */
function pick(inv,list,n,used){
  const av=list.filter(k=>inv[k]&&!used.has(k)).map(k=>({k,s:urg(inv[k].d)+Math.random()*2.2})).sort((a,b)=>b.s-a.s);
  return av.slice(0,n||1).map(o=>o.k);
}
const one=(inv,list,used)=>pick(inv,list,1,used)[0];

function aromaStep(ar){if(!ar.length)return null;return`Émince ${joinFr(ar.map(k=>the(k)))} et fais revenir 3 min dans un filet d'huile.`}
function vegSteps(vs){
  if(!vs.length)return[];const cut=vs.filter(v=>!NOCUT[v]);const t=Math.max(...vs.map(v=>VT[v]||6));
  const out=[];if(cut.length)out.push(`Coupe ${joinFr(cut.map(v=>the(v,QTY[v]&&+QTY[v]>1)))} en petits morceaux.`);
  out.push(`Ajoute ${joinFr(vs.map(v=>the(v,QTY[v]&&+QTY[v]>1)))} et laisse cuire ${t} min en remuant.`);return out;
}
function withPh(prot,vs){const p=[];if(prot)p.push(au(prot));vs.forEach(v=>p.push(au(v,true)));return joinFr(p)}

/* ---------- Modèles de plats ---------- */
const T=[];
// Pâtes crémeuses
T.push(inv=>{const u=new Set();const st=one(inv,G.pasta,u);if(!st)return;u.add(st);
  const cr=inv["crème fraîche"]?"crème fraîche":null;const ch=one(inv,G.cheese,u);if(!cr&&!ch)return;if(cr)u.add(cr);if(ch)u.add(ch);
  const pr=one(inv,[...G.meat,...G.fish,...G.cold],u);if(pr)u.add(pr);const vs=pick(inv,G.veg.filter(v=>v!=="potiron"&&v!=="endive"),pr?1:2,u);vs.forEach(v=>u.add(v));if(!pr&&!vs.length)return;
  const ar=pick(inv,G.aroma,1,u);ar.forEach(a=>u.add(a));
  const ttl=`${capf(st)} crémeu${st==="gnocchis"?"x":"ses"} ${withPh(pr,vs)}`;
  const s=[];if(st!=="gnocchis")s.push(COOK[st]);const a=aromaStep(ar);if(a)s.push(a);if(pr)s.push(PREP[pr]);s.push(...vegSteps(vs));
  if(cr)s.push(`Verse la crème, sale, poivre et laisse épaissir 2 min à feu doux.`);else s.push(`Ajoute un petit verre d'eau de cuisson pour faire une sauce.`);
  if(st==="gnocchis")s.push(COOK.gnocchis);
  s.push(`Mélange ${st==="gnocchis"?"les gnocchis":`avec ${the(st)} égoutté${st==="riz"?"":"es"}`}${ch?`, ajoute ${the(ch)}`:""} et sers bien chaud.`);
  return{t:ttl,e:"🍝",tm:25,c:"Pâtes & riz",u,s};
});
// Pâtes / riz à la tomate
T.push(inv=>{const u=new Set();const st=one(inv,["pâtes","riz","gnocchis","nouilles"],u);if(!st)return;u.add(st);
  const sauce=inv["sauce tomate"]?"sauce tomate":(inv["tomate"]?"tomate":null);if(!sauce)return;u.add(sauce);
  const pr=one(inv,[...G.meat,...G.fish,"thon",...G.legum],u);if(pr)u.add(pr);const vs=pick(inv,G.veg.filter(v=>v!=="tomate"&&v!=="endive"),pr?1:2,u);vs.forEach(v=>u.add(v));
  const ch=one(inv,G.cheese,u);if(ch)u.add(ch);const ar=pick(inv,G.aroma,2,u);ar.forEach(a=>u.add(a));
  const ttl=`${capf(st)} à la tomate${pr||vs.length?", "+withPh(pr,vs):""}`;
  const s=[COOK[st]];const a=aromaStep(ar);if(a)s.push(a);if(pr)s.push(PREP[pr]);s.push(...vegSteps(vs));
  s.push(sauce==="tomate"?`Coupe les tomates en dés, ajoute-les avec un peu de sel, de poivre et d'herbes, laisse compoter 10 min.`:`Verse la sauce tomate, ajoute sel, poivre et herbes, laisse mijoter 10 min.`);
  s.push(`Mélange avec ${the(st)}${ch?` et parsème ${de(ch).replace(/^de /,"de ")}`:""}.`.replace("parsème de ","parsème de ").replace("parsème d'","parsème d'"));
  return{t:ttl,e:"🍅",tm:25,c:"Pâtes & riz",u,s};
});
// Gratin
T.push(inv=>{const u=new Set();const base=one(inv,["pomme de terre","pâtes","courgette","brocoli","chou-fleur","potiron","endive","aubergine"],u);if(!base)return;u.add(base);
  const liq=inv["crème fraîche"]?"crème fraîche":inv["lait"]?"lait":null;const ch=one(inv,G.cheese.filter(c=>c!=="feta"),u);if(!liq||!ch)return;u.add(liq);u.add(ch);
  const pr=one(inv,["jambon","lardons","bœuf haché","poulet","thon","chorizo","saumon"],u);if(pr)u.add(pr);
  const ttl=`Gratin ${de(base==="pomme de terre"?"pommes de terre":base==="pâtes"?"pâtes":plur(base)==="chou-fleurs"?"chou-fleur":base)}${pr?" "+au(pr):""}`;
  const s=["Préchauffe le four à 200 °C."];
  if(base==="pomme de terre")s.push("Épluche les pommes de terre et coupe-les en fines rondelles.");
  else if(base==="pâtes")s.push("Fais cuire les pâtes 8 min (un peu moins que d'habitude), puis égoutte-les.");
  else s.push(`Coupe ${the(base,true)} en morceaux et fais-les précuire 6 min à la poêle avec un filet d'huile.`);
  if(pr&&PREP[pr])s.push(PREP[pr]);
  s.push(`Mets tout dans un plat, verse ${liq==="lait"?"le lait":"la crème"}, sale, poivre (une pointe de muscade si tu as).`);
  s.push(`Recouvre ${de(ch)} et enfourne ${base==="pomme de terre"?"45":"25"} min jusqu'à ce que ce soit bien doré.`);
  return{t:ttl,e:"🧀",tm:base==="pomme de terre"?60:40,c:"Four",u,s};
});
// Omelette / tortilla
T.push(inv=>{const u=new Set();if(!inv["œufs"])return;u.add("œufs");
  const pdt=inv["pomme de terre"]&&Math.random()<.6;if(pdt)u.add("pomme de terre");
  const pr=one(inv,["lardons","jambon","chorizo","saumon","poulet","thon"],u);if(pr)u.add(pr);
  const vs=pick(inv,["champignons","courgette","poivron","tomate","épinards","oignon","poireau"],pdt?1:2,u);vs.forEach(v=>u.add(v));
  const ch=one(inv,G.cheese,u);if(ch)u.add(ch);const hb=one(inv,G.herb,u);if(hb)u.add(hb);
  if(!pdt&&!pr&&!vs.length&&!ch)return;
  const ttl=pdt?`Tortilla de pommes de terre${pr||vs.length?" "+withPh(pr,vs):""}`:`Omelette ${withPh(pr,vs.concat(ch?[ch]:[]))}`;
  const s=[];if(pdt)s.push("Coupe les pommes de terre en fines rondelles et fais-les cuire 15 min à la poêle avec un filet d'huile, à couvert.");
  if(pr&&PREP[pr])s.push(PREP[pr]);if(vs.length)s.push(...vegSteps(vs));
  s.push(`Bats les œufs avec du sel, du poivre${hb?" et "+the(hb)+" ciselé":""}.`);
  s.push(`Verse les œufs dans la poêle sur la garniture et laisse cuire ${pdt?"6 min à feu doux, puis retourne-la avec une assiette 2 min":"3 min en ramenant les bords vers le centre"}.`);
  s.push(`${ch?`Ajoute ${the(ch)}, `:""}${pdt?"coupe en parts et sers chaud ou tiède.":"plie l'omelette en deux et sers tout de suite."}`.replace(/^./,c=>c.toUpperCase()));
  return{t:ttl,e:"🍳",tm:pdt?30:15,c:"Rapide",u,s};
});
// Quiche / tarte
T.push(inv=>{const u=new Set();const pa=one(inv,["pâte brisée","pâte feuilletée"],u);if(!pa||!inv["œufs"])return;u.add(pa);u.add("œufs");
  const liq=inv["crème fraîche"]?"crème fraîche":inv["lait"]?"lait":null;if(!liq)return;u.add(liq);
  const pr=one(inv,["lardons","jambon","saumon","thon","chorizo","poulet"],u);if(pr)u.add(pr);
  const vs=pick(inv,["poireau","courgette","champignons","épinards","tomate","poivron","brocoli","oignon"],pr?1:2,u);vs.forEach(v=>u.add(v));
  if(!pr&&!vs.length)return;const ch=one(inv,G.cheese,u);if(ch)u.add(ch);
  const ttl=`Quiche ${withPh(pr,vs.concat(ch&&ch!=="fromage râpé"?[ch]:[]))}`;
  const s=["Préchauffe le four à 180 °C. Étale la pâte dans un moule et pique le fond à la fourchette."];
  if(pr&&PREP[pr])s.push(PREP[pr]);s.push(...vegSteps(vs));
  s.push(`Bats 3 œufs avec ${liq==="lait"?"25 cl de lait":"la crème"}, du sel et du poivre.`);
  s.push(`Répartis la garniture${ch?" et "+the(ch):""} sur la pâte, verse le mélange.`);
  s.push("Enfourne 35 min jusqu'à ce que ce soit bien doré.");
  return{t:ttl,e:"🥧",tm:55,c:"Four",u,s};
});
// Pizza
T.push(inv=>{const u=new Set();if(!inv["pâte à pizza"])return;u.add("pâte à pizza");
  const base=inv["sauce tomate"]?"sauce tomate":inv["crème fraîche"]?"crème fraîche":inv["tomate"]?"tomate":null;if(!base)return;u.add(base);
  const ch=one(inv,["mozzarella","fromage râpé","chèvre","reblochon","fromage","comté","feta"],u);if(!ch)return;u.add(ch);
  const tops=pick(inv,["jambon","chorizo","lardons","thon","poulet","champignons","poivron","oignon","tomate","olives","courgette"],3,u);tops.forEach(t=>u.add(t));
  const ttl=`Pizza ${withPh(null,tops.concat([ch]))}`.replace("Pizza aux","Pizza aux");
  const s=["Préchauffe le four à 240 °C et déroule la pâte sur la plaque.",
   base==="tomate"?"Coupe les tomates en fines rondelles et étale-les sur la pâte avec un filet d'huile.":base==="crème fraîche"?"Étale la crème sur la pâte, sale et poivre.":"Étale la sauce tomate avec un peu d'herbes.",
   tops.length?`Ajoute ${joinFr(tops.map(t=>the(t)))} coupés en morceaux.`:"Ajoute ce qui te fait envie.",
   `Termine avec ${the(ch)} et enfourne 12 à 15 min.`];
  if(tops.includes("poulet"))s.splice(2,0,"Fais d'abord cuire le poulet 6 min à la poêle.");
  return{t:ttl,e:"🍕",tm:25,c:"Four",u,s};
});
// Poêlée / sauté
T.push(inv=>{const u=new Set();const pr=one(inv,[...G.meat.filter(m=>m!=="lardons"&&m!=="chorizo"),...G.fish],u);if(!pr)return;u.add(pr);
  const vs=pick(inv,G.veg.filter(v=>v!=="endive"),2,u);if(!vs.length)return;vs.forEach(v=>u.add(v));
  const soja=inv["sauce soja"]&&Math.random()<.7;if(soja)u.add("sauce soja");
  const st=one(inv,soja?["nouilles","riz"]:["riz","pâtes","semoule","pomme de terre"],u);if(st)u.add(st);
  const ar=pick(inv,G.aroma,2,u);ar.forEach(a=>u.add(a));
  const ttl=soja&&st==="nouilles"?`Nouilles sautées ${withPh(pr,vs)}`:`Poêlée ${de(pr)} ${withPh(null,vs)}${st?" "+AVEC[st]:""}`;
  const s=[];if(st)s.push(COOK[st]);const a=aromaStep(ar);if(a)s.push(a);s.push(PREP[pr]);s.push(...vegSteps(vs));
  s.push(soja?"Ajoute la sauce soja et fais sauter 2 min à feu vif.":"Sale, poivre, ajoute des herbes si tu as et mélange bien.");
  if(st)s.push(st==="nouilles"?"Ajoute les nouilles, mélange et sers.":`Sers avec ${st==="pomme de terre"?"les pommes de terre":the(st)}.`);
  return{t:ttl,e:soja?"🥢":"🍳",tm:30,c:G.fish.includes(pr)?"Poisson":"Viande",u,s};
});
// Curry coco
T.push(inv=>{const u=new Set();if(!inv["lait de coco"])return;u.add("lait de coco");
  const pr=one(inv,["poulet","crevettes","saumon","cabillaud","porc","pois chiches","lentilles"],u);if(pr)u.add(pr);
  const vs=pick(inv,["courgette","carotte","poivron","épinards","brocoli","chou-fleur","potiron","petits pois","champignons","tomate"],pr?2:3,u);vs.forEach(v=>u.add(v));if(!pr&&vs.length<2)return;
  const st=one(inv,["riz","semoule","nouilles"],u);if(st)u.add(st);const ar=pick(inv,G.aroma,2,u);ar.forEach(a=>u.add(a));
  const vn=vs.map(v=>PLURAL_SHOW[v]?plur(v):v);const ttl=`Curry ${pr?de(pr)+" ":"de légumes "}au lait de coco${pr&&vs.length?", "+joinFr(vn):""}`;
  const s=[];if(st)s.push(COOK[st]);const a=aromaStep(ar);if(a)s.push(a);if(pr)s.push(PREP[pr]);s.push(...vegSteps(vs));
  s.push("Ajoute 1 cuillère à soupe de curry (ou d'épices), mélange 1 min.");
  s.push("Verse le lait de coco et laisse mijoter 15 min à feu doux. Sale.");
  if(st)s.push(`Sers avec ${the(st)}.`);
  return{t:ttl,e:"🍛",tm:35,c:"Viande",u,s};
});
// Riz sauté
T.push(inv=>{const u=new Set();if(!inv["riz"])return;u.add("riz");
  const eg=inv["œufs"]?"œufs":null;const pr=one(inv,["jambon","poulet","crevettes","lardons","saucisses","porc"],u);if(!eg&&!pr)return;if(eg)u.add(eg);if(pr)u.add(pr);
  const vs=pick(inv,["petits pois","carotte","poivron","courgette","champignons","maïs","oignon","légumes surgelés"],2,u);if(!vs.length)return;vs.forEach(v=>u.add(v));
  if(inv["sauce soja"])u.add("sauce soja");
  const ttl=`Riz sauté ${withPh(pr,vs)}`;
  const s=[COOK.riz+" Laisse-le refroidir un peu."];if(eg)s.push("Bats 2 œufs et fais une omelette fine, puis coupe-la en lanières.");if(pr&&PREP[pr])s.push(PREP[pr]);s.push(...vegSteps(vs));
  s.push(`Ajoute le riz${inv["sauce soja"]?" et la sauce soja":", du sel et du poivre"}, fais sauter 5 min à feu vif.`);if(eg)s.push("Ajoute l'omelette, mélange et sers.");
  return{t:ttl,e:"🍚",tm:25,c:"Pâtes & riz",u,s};
});
// Velouté
T.push(inv=>{const u=new Set();const vs=pick(inv,G.soup,3,u);if(vs.length<2)return;vs.forEach(v=>u.add(v));
  const ar=pick(inv,["oignon","échalote","poireau"].filter(a=>!u.has(a)),1,u);ar.forEach(a=>u.add(a));
  const cr=inv["crème fraîche"]?"crème fraîche":inv["fromage blanc"]?"fromage blanc":null;if(cr)u.add(cr);const ch=one(inv,["chèvre","fromage râpé","comté","parmesan"],u);if(ch&&Math.random()<.5)u.add(ch);
  const main=vs.slice(0,2);
  const vp=v=>PLURAL_SHOW[v]?plur(v):v;const ttl=`Velouté ${de(vp(main[0]))} et ${de(vp(main[1]))}`;
  const s=[`Épluche et coupe ${joinFr(vs.map(v=>the(v,true)))} en morceaux.`];const a=aromaStep(ar);if(a)s.push(a.replace("dans un filet d'huile","dans une casserole avec un filet d'huile"));
  s.push("Ajoute les légumes, couvre d'eau (environ 1 litre) avec un cube de bouillon si tu as, et laisse cuire 25 min.");
  s.push(`Mixe${cr?" avec "+the(cr):""}, sale et poivre.`);if(u.has(ch))s.push(`Sers bien chaud avec ${the(ch)} par-dessus.`);
  return{t:ttl,e:"🥣",tm:40,c:"Soupes",u,s};
});
// Wraps / quesadillas
T.push(inv=>{const u=new Set();if(!inv["tortillas"])return;u.add("tortillas");
  const pr=one(inv,["poulet","bœuf haché","thon","jambon","saumon","crevettes","haricots rouges"],u);if(!pr)return;u.add(pr);
  const ch=one(inv,["fromage râpé","mozzarella","cheddar","fromage","chèvre","feta"],u);if(ch)u.add(ch);
  const vs=pick(inv,["salade","tomate","avocat","poivron","maïs","concombre","oignon"],2,u);vs.forEach(v=>u.add(v));
  const q=ch&&Math.random()<.55;
  const ttl=q?`Quesadillas ${withPh(pr,vs)}`:`Wraps ${withPh(pr,vs)}`;
  const s=[];if(PREP[pr])s.push(PREP[pr]+(pr==="poulet"||pr==="bœuf haché"?" Ajoute un peu d'épices.":""));
  if(vs.length)s.push(`Coupe ${joinFr(vs.map(v=>the(v,true)))} en petits morceaux.`);
  if(q){s.push(`Sur une tortilla : ${the(ch)}, la garniture, encore un peu de fromage, puis une autre tortilla.`);s.push("Fais cuire 2 min de chaque côté à la poêle, coupe en 4 et sers.")}
  else{s.push("Fais chauffer les tortillas 20 s à la poêle.");s.push(`Garnis-les${ch?" avec "+the(ch):""}, roule-les bien serré et coupe-les en deux.`)}
  return{t:ttl,e:"🌯",tm:20,c:"Rapide",u,s};
});
// Salade composée
T.push(inv=>{const u=new Set();const base=inv["salade"]?"salade":inv["tomate"]?"tomate":null;if(!base)return;u.add(base);
  const pr=one(inv,["thon","œufs","poulet","feta","mozzarella","jambon","chèvre","saumon","pois chiches","lentilles"],u);if(!pr)return;u.add(pr);
  const vs=pick(inv,["tomate","concombre","avocat","maïs","poivron","carotte","champignons"].filter(v=>v!==base),2,u);vs.forEach(v=>u.add(v));
  const st=Math.random()<.5?one(inv,["pâtes","riz","semoule","pomme de terre"],u):null;if(st)u.add(st);
  const ttl=st?`Salade ${de(st==="pomme de terre"?"pommes de terre":st)} ${withPh(pr,vs)}`:`Salade ${base==="tomate"?"de tomates ":""}${withPh(pr,vs)}`;
  const s=[];if(st)s.push(st==="pomme de terre"?"Fais cuire les pommes de terre 20 min dans l'eau salée, laisse tiédir et coupe-les.":COOK[st]+" Rince à l'eau froide.");
  if(pr==="œufs")s.push("Fais cuire 3 œufs 10 min dans l'eau bouillante, refroidis-les et écale-les.");else if(pr==="poulet")s.push("Fais cuire le poulet 10 min à la poêle, puis coupe-le en lamelles.");else if(PREP[pr])s.push(PREP[pr]);
  s.push(`Coupe ${joinFr([base,...vs].map(v=>the(v,true)))} en morceaux.`);
  s.push("Fais une vinaigrette : 3 cuillères d'huile, 1 de vinaigre, sel, poivre (et un peu de moutarde si tu as).");
  s.push(`Mélange tout${pr!=="œufs"&&pr!=="poulet"?" avec "+the(pr):""} et sers bien frais.`);
  return{t:ttl,e:"🥗",tm:20,c:"Veggie",u,s};
});
// Croque / tartines
T.push(inv=>{const u=new Set();const br=one(inv,["pain de mie","pain"],u);if(!br)return;u.add(br);
  const ch=one(inv,["fromage râpé","mozzarella","chèvre","comté","fromage","reblochon"],u);if(!ch)return;u.add(ch);
  const pr=one(inv,["jambon","thon","saumon","chorizo"],u);if(pr)u.add(pr);const vs=pick(inv,["tomate","champignons","courgette","épinards","oignon"],1,u);vs.forEach(v=>u.add(v));
  const croque=br==="pain de mie";if(!croque&&!pr&&!vs.length)return;
  const ttl=croque?(pr==="jambon"&&!vs.length?"Croque-monsieur maison":`Croque ${withPh(pr,vs.concat([ch]))}`):`Tartines gratinées ${withPh(pr,vs.concat([ch]))}`;
  const s=["Préchauffe le four à 200 °C."];if(vs.length)s.push(`Coupe ${the(vs[0],true)} en fines tranches.`);
  s.push(croque?`Garnis 2 tranches de pain de mie${pr?" avec "+the(pr):""}${vs.length?", "+the(vs[0],true):""} et un peu ${de(ch)}, referme avec les 2 autres.`:`Garnis les tranches de pain${pr?" avec "+the(pr):""}${vs.length?", "+the(vs[0],true):""}.`);
  s.push(`Recouvre ${de(ch)}${inv["crème fraîche"]?" et d'une cuillère de crème":""}.`);s.push("Enfourne 10 à 12 min jusqu'à ce que ce soit bien doré.");
  if(inv["crème fraîche"])u.add("crème fraîche");
  return{t:ttl,e:"🥪",tm:15,c:"Rapide",u,s};
});
// Galettes
T.push(inv=>{const u=new Set();if(!inv["galettes de sarrasin"])return;u.add("galettes de sarrasin");
  const f=pick(inv,["œufs","jambon","fromage râpé","champignons","lardons","chèvre","épinards","saumon","emmental"],3,u);if(!f.length)return;f.forEach(k=>u.add(k));
  const ttl=`Galettes ${withPh(null,f.filter(k=>k!=="œufs"))}${f.includes("œufs")?(f.length>1?" et œuf":" à l'œuf"):""}`.replace("Galettes  ","Galettes ").trim();
  const s=[];f.filter(k=>PREP[k]&&k!=="jambon").forEach(k=>s.push(PREP[k]));if(f.includes("champignons"))s.push("Fais revenir les champignons émincés 6 min.");
  s.push("Fais chauffer une grande poêle avec un peu de beurre et pose une galette.");
  s.push(`Garnis avec ${joinFr(f.filter(k=>k!=="œufs").map(k=>the(k)))||"ce que tu as"}${f.includes("œufs")?", casse un œuf au centre et couvre 3 min":" et laisse chauffer 3 min"}.`);
  s.push("Replie les bords en carré et sers. Recommence avec la deuxième.");
  return{t:ttl,e:"🥞",tm:15,c:"Rapide",u,s};
});
// Desserts
T.push(inv=>{const u=new Set();const fr=one(inv,["pomme","poire","banane","fraises"],u);if(!fr||!inv["farine"]||!inv["beurre"]||!inv["sucre"])return;[fr,"farine","beurre","sucre"].forEach(k=>u.add(k));
  return{t:`Crumble ${au(fr,true)}`,e:"🍎",tm:45,c:"Dessert",u,w:.35,s:["Préchauffe le four à 180 °C.",`Coupe ${the(fr,true)} en morceaux et mets-les dans un plat${inv["chocolat"]?" avec quelques carrés de chocolat":""}.`,"Mélange du bout des doigts 100 g de farine, 60 g de sucre et 60 g de beurre mou jusqu'à avoir du sable.","Recouvre les fruits avec ce sable.","Enfourne 30 min jusqu'à ce que ce soit doré."]};
});
T.push(inv=>{const u=new Set();if(!inv["œufs"]||!inv["farine"]||!inv["lait"])return;["œufs","farine","lait"].forEach(k=>u.add(k));
  const gar=pick(inv,["banane","chocolat","pâte à tartiner","fraises","pomme","confiture","miel"],2,u);gar.forEach(k=>u.add(k));
  return{t:`Crêpes${gar.length?" "+withPh(null,gar):""}`,e:"🥞",tm:30,c:"Dessert",u,w:.35,s:["Mélange 250 g de farine avec une pincée de sel (et un peu de sucre si tu as), fais un puits.","Ajoute 3 œufs et verse 50 cl de lait petit à petit en fouettant.","Laisse reposer 15 min si tu as le temps.","Fais cuire les crêpes 1 min de chaque côté dans une poêle légèrement graissée.",gar.length?`Garnis avec ${joinFr(gar.map(k=>the(k,true)))}.`:"Garnis avec ce qui te fait envie."]};
});
T.push(inv=>{const u=new Set();if(!inv["chocolat"]||!inv["œufs"]||!inv["beurre"])return;["chocolat","œufs","beurre"].forEach(k=>u.add(k));if(inv["sucre"])u.add("sucre");if(inv["farine"])u.add("farine");
  return{t:"Fondant au chocolat",e:"🍫",tm:35,c:"Dessert",u,w:.35,s:["Préchauffe le four à 180 °C et beurre un moule.","Fais fondre 150 g de chocolat avec 100 g de beurre.",`Fouette 3 œufs${inv["sucre"]?" avec 80 g de sucre":""}, ajoute le chocolat fondu${inv["farine"]?" puis 40 g de farine":""}.`,"Verse dans le moule et enfourne 20 min : le centre doit rester un peu tremblotant."]};
});
// Assiette simple (filet de sécurité)
T.push(inv=>{const u=new Set();const pr=one(inv,[...G.meat,...G.fish,"cordon bleu","œufs"],u);if(!pr)return;u.add(pr);
  const st=one(inv,G.starch,u);const vs=pick(inv,G.veg,1,u);if(!st&&!vs.length)return;if(st)u.add(st);vs.forEach(v=>u.add(v));
  const ttl=`${capf(pr==="œufs"?"œufs au plat":pr)}${vs.length?" "+au(vs[0],true):""}${st?" "+AVEC[st]:""}`;
  const s=[];if(st)s.push(COOK[st]);if(pr==="œufs")s.push("Fais cuire 2 œufs au plat par personne dans un filet d'huile, 3 min.");else if(pr==="cordon bleu")s.push("Fais dorer les cordons bleus 6 min de chaque côté.");else s.push(PREP[pr]);
  s.push(...vegSteps(vs));s.push("Sale, poivre et sers tout ensemble.");
  return{t:ttl,e:"🍽️",tm:25,c:"Rapide",u,s,w:.6};
});

/* ---------- Générateur ---------- */
function build(r,inv){
  const keys=[...r.u].filter(k=>k!=="sauce soja"||true);
  const i=keys.map(k=>line(k)).concat(r.c==="Dessert"?[]:["1 cs huile","sel","poivre"]);
  const score=keys.reduce((a,k)=>a+urg(inv[k]?inv[k].d:60),0)+keys.length*.5+Math.random()*2.5;
  return{sig:r.e+"|"+[...r.u].sort().join(","),t:r.t.replace(/\s+/g," ").trim(),e:r.e,tm:r.tm,c:r.c,i,s:r.s||[],score:score*(r.w||1),keys};
}
window.inventRecipes=function(items,n){
  const inv=makeInv(items);if(!Object.keys(inv).length)return[];
  const seen=new Map();
  for(let round=0;round<6;round++)T.forEach(f=>{let r;try{r=f(inv)}catch(e){r=null}if(!r)return;if(!r.s){/* les modèles qui écrivent leurs étapes plus haut */}
    const b=build(r,inv);const key=b.sig;if(!seen.has(key)||seen.get(key).score<b.score)seen.set(key,b)});
  return[...seen.values()].filter(r=>r.s&&r.s.length).sort((a,b)=>b.score-a.score).slice(0,n||12);
};
})();
