import { unzipSync, zipSync, strFromU8, strToU8 } from "fflate";
import { z } from "zod";

export const editSchema=z.object({id:z.string().min(1).max(80),block_id:z.string().max(120),operation:z.enum(["replace","delete","insert"]),old_text:z.string().max(10000),new_text:z.string().max(10000),context_before:z.string().max(2000),context_after:z.string().max(2000),page:z.number().int().min(1),reason:z.string().max(4000),needs_review:z.boolean()}).strict();
export const resultSchema=z.object({edits:z.array(editSchema).max(1500),warnings:z.array(z.string().max(4000)).max(100)}).strict();
export type Edit=z.infer<typeof editSchema>;
export type Telemetry={mode:"offline_demo"|"live_api"|"codex_cli";requested_model:string|null;returned_model:string|null;api_request_seconds:number|null;total_proposal_seconds?:number;demo_load_seconds?:number;export_seconds?:number;cli_reported_model?:string|null;model_identity_source?:string;response_id?:string;provider_request_id?:string|null;usage?:{input_tokens:number;output_tokens:number;total_tokens:number}|null;recorded_at?:string;prompt_version?:string;provider?:string;server_total_seconds?:number|null;load_seconds?:number|null;prompt_seconds?:number|null;generation_seconds?:number|null;pages_processed?:number[];scan_pages?:number};
export type Plan={schema_version?:number;source_sha256:string;edits:Edit[];warnings:string[];metadata?:Record<string,unknown>};
export type Block={id:string;text:string;editable:boolean;blocked_reason:string|null};
export type Snapshot={bytes:Uint8Array;hash:string;blocks:Block[];hasRevisions:boolean};
const W="http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const XML="http://www.w3.org/XML/1998/namespace";
const REV=new Set(["ins","del","moveFrom","moveTo","pPrChange","rPrChange","tblPrChange","trPrChange","tcPrChange","sectPrChange","numberingChange","cellDel","cellIns","cellMerge"]);
const BLOCKED=new Set([...REV,"fldChar","instrText","hyperlink","sdt","drawing","pict","object","br","tab","cr","sym","bookmarkStart","bookmarkEnd","commentRangeStart","commentRangeEnd","commentReference","footnoteReference","endnoteReference","sectPr","lastRenderedPageBreak","txbxContent","altChunk"]);

// Inspect the central directory before decompression: fixed, bounded classic ZIP only.
function checkZip(bytes:Uint8Array){
 if(bytes.length>20*1024*1024)throw Error("Word file exceeds the 20 MB pilot limit.");
 const d=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);let end=-1;
 for(let i=bytes.length-22;i>=Math.max(0,bytes.length-65557);i--){if(d.getUint32(i,true)===0x06054b50&&i+22+d.getUint16(i+20,true)===bytes.length){end=i;break;}}
 if(end<0||end!==bytes.length-22||d.getUint16(end+20,true)!==0||(end>=20&&d.getUint32(end-20,true)===0x07064b50))throw Error("Use a standard DOCX ZIP package without archive comments or ZIP64.");
 const count=d.getUint16(end+10,true),start=d.getUint32(end+16,true),size=d.getUint32(end+12,true);
 if(count>1500||d.getUint16(end+4,true)||d.getUint16(end+6,true)||count!==d.getUint16(end+8,true)||start+size!==end)throw Error("Unsupported or oversized Word package.");
 let pos=start,total=0;const names=new Set<string>();const manifest=new Map<string,number>();const ranges:{lo:number;hi:number}[]=[];
 for(let i=0;i<count;i++){
  if(pos+46>end||d.getUint32(pos,true)!==0x02014b50)throw Error("Invalid ZIP directory.");
  const flags=d.getUint16(pos+8,true),method=d.getUint16(pos+10,true),compressed=d.getUint32(pos+20,true),uncompressed=d.getUint32(pos+24,true),nl=d.getUint16(pos+28,true),xl=d.getUint16(pos+30,true),cl=d.getUint16(pos+32,true),offset=d.getUint32(pos+42,true);
  if(pos+46+nl+xl+cl>end)throw Error("Invalid ZIP entry.");
  const name=strFromU8(bytes.subarray(pos+46,pos+46+nl));
  if(names.has(name)||name.includes("..")||name.startsWith("/")||name.includes("\\")||flags&1||![0,8].includes(method)||(method===0&&compressed!==uncompressed)||uncompressed>12*1024*1024||compressed>bytes.length||offset+30>start)throw Error("Unsupported, duplicate, or oversized ZIP entry.");
  if(d.getUint32(offset,true)!==0x04034b50||d.getUint16(offset+6,true)!==flags||d.getUint16(offset+8,true)!==method)throw Error("ZIP headers disagree.");
  const lnl=d.getUint16(offset+26,true),lxl=d.getUint16(offset+28,true);
  if(offset+30+lnl+lxl+compressed>start||strFromU8(bytes.subarray(offset+30,offset+30+lnl))!==name)throw Error("Invalid ZIP entry bounds.");
  total+=uncompressed;if(total>40*1024*1024)throw Error("Expanded Word file exceeds the 40 MB pilot limit.");
  names.add(name);manifest.set(name,uncompressed);ranges.push({lo:offset,hi:offset+30+lnl+lxl+compressed});pos+=46+nl+xl+cl;
 }
 if(pos!==end||!names.has("word/document.xml")||!names.has("[Content_Types].xml"))throw Error("Required Word package parts are missing.");
 if([...names].some(n=>/vbaProject|_xmlsignatures/i.test(n)))throw Error("Signed documents and macro-enabled packages are outside this pilot.");
 ranges.sort((a,b)=>a.lo-b.lo);if(ranges.some((r,i)=>i>0&&r.lo<ranges[i-1].hi))throw Error("ZIP members overlap.");
 return manifest;
}
function parseXml(xml:string){if(/<!DOCTYPE|<!ENTITY/i.test(xml))throw Error("XML entities are unsupported.");const doc=new DOMParser().parseFromString(xml,"application/xml");if(doc.getElementsByTagName("parsererror").length)throw Error("Invalid Word XML.");return doc;}
function open(bytes:Uint8Array){const manifest=checkZip(bytes);const zip=unzipSync(bytes);if(Object.keys(zip).length!==manifest.size||Object.entries(zip).some(([name,data])=>manifest.get(name)!==data.length))throw Error("Decompressed package does not match its validated directory.");const types=strFromU8(zip["[Content_Types].xml"]);if(!types.includes("application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"))throw Error("Use a standard .docx Word document.");const doc=parseXml(strFromU8(zip["word/document.xml"]));return {zip,doc,paragraphs:Array.from(doc.getElementsByTagNameNS(W,"p"))};}
function textNodes(p:Element){return Array.from(p.getElementsByTagNameNS(W,"t"));}
function text(p:Element){return textNodes(p).map(t=>t.textContent??"").join("");}
function blocker(p:Element):string|null{
 for(const e of Array.from(p.getElementsByTagName("*"))){if(e.namespaceURI!==W||BLOCKED.has(e.localName))return `Contains unsupported ${e.localName}`;}
 for(const e of Array.from(p.children)){if(!["pPr","r"].includes(e.localName))return `Contains unsupported ${e.localName}`;if(e.localName==="r"&&Array.from(e.children).some(c=>!["rPr","t"].includes(c.localName)))return "Contains non-text run content";}
 for(let a=p.parentElement;a;a=a.parentElement){if(BLOCKED.has(a.localName))return `Inside ${a.localName}`;if(["tr","tc","tbl"].includes(a.localName)){for(const prop of Array.from(a.children).filter(c=>c.localName.endsWith("Pr"))){if(Array.from(prop.getElementsByTagName("*")).some(e=>REV.has(e.localName)))return "Table has tracked changes";}}}
 return null;
}
// Validate again on the local renderer, which receives bytes from the browser.
export function validateRenderPackage(bytes:Uint8Array){
 const manifest=checkZip(bytes),zip=unzipSync(bytes);
 if(Object.keys(zip).length!==manifest.size||Object.entries(zip).some(([name,data])=>manifest.get(name)!==data.length))throw Error("Invalid Word package.");
 if(!strFromU8(zip["[Content_Types].xml"]).includes("application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"))throw Error("Use a standard DOCX file.");
 for(const [name,data] of Object.entries(zip)){
  if(/activeX|embeddings|vbaProject/i.test(name))throw Error("Embedded active content is not supported by PDF export.");
  if(!/\.(xml|rels)$/.test(name))continue;const xml=strFromU8(data);
  if(/<!DOCTYPE|<!ENTITY|\bDDE(?:AUTO)?\b/i.test(xml))throw Error("Active document content is unsupported.");
  if(name.endsWith(".rels")&&/<(?:\w+:)?Relationship\b[^>]*TargetMode\s*=\s*["']External["'][^>]*>/gi.test(xml)){
   for(const relationship of xml.match(/<(?:\w+:)?Relationship\b[^>]*>/gi)||[]){if(/TargetMode\s*=\s*["']External["']/i.test(relationship)&&! /Type\s*=\s*["'][^"']*\/hyperlink["']/i.test(relationship))throw Error("Linked external content is unsupported by PDF export.");}
  }
 }
}
export async function sha256(bytes:Uint8Array){return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",new Uint8Array(bytes)))).map(x=>x.toString(16).padStart(2,"0")).join("");}
export async function inspect(bytes:Uint8Array):Promise<Snapshot>{const {doc,paragraphs}=open(bytes);const fields=new Set<Element>();let depth=0;for(const e of Array.from(doc.getElementsByTagName("*"))){if(e.namespaceURI!==W)continue;if(e.localName==="p"&&depth>0)fields.add(e);if(e.localName==="fldChar"){for(let p:Element|null=e.parentElement;p;p=p.parentElement){if(p.localName==="p"){fields.add(p);break;}}const type=e.getAttributeNS(W,"fldCharType");if(type==="begin")depth++;else if(type==="end")depth=Math.max(0,depth-1);}}return{bytes,hash:await sha256(bytes),hasRevisions:Array.from(doc.getElementsByTagName("*")).some(e=>REV.has(e.localName)),blocks:paragraphs.map((p,i)=>{const blocked_reason=fields.has(p)?"Paragraph contains a Word field result":blocker(p);return{id:`word/document.xml:p${String(i+1).padStart(6,"0")}`,text:text(p),editable:!blocked_reason,blocked_reason};})};}
function locate(block:Block,e:Edit){
 if(!block.editable)throw Error(block.blocked_reason??"Unsupported paragraph.");
 if(e.operation==="replace"&&e.old_text===e.new_text)throw Error("This proposal makes no text change.");
 if(e.needs_review)throw Error("Unresolved markup requires human editing.");
 if(e.operation==="insert"?(e.old_text!==""||!e.new_text||(!e.context_before&&!e.context_after)):!e.old_text||(e.operation==="replace"?!e.new_text:e.new_text!==""))throw Error("Invalid text operation.");
 if(/[\r\n\t\x00-\x08\x0b\x0c\x0e-\x1f]/.test(e.new_text))throw Error("Paragraph and control-character edits require manual review.");
 const hits:number[]=[];
 for(let i=0;i<=block.text.length;i++){if(block.text.startsWith(e.old_text,i)&&block.text.slice(Math.max(0,i-e.context_before.length),i)===e.context_before&&block.text.slice(i+e.old_text.length,i+e.old_text.length+e.context_after.length)===e.context_after)hits.push(i);}
 if(hits.length!==1)throw Error(hits.length?"Target matches more than once.":"Exact target and context were not found.");
 return{start:hits[0],end:hits[0]+e.old_text.length,edit:e};
}
export function editIssue(snapshot:Snapshot,e:Edit){try{const b=snapshot.blocks.find(b=>b.id===e.block_id);if(!b)throw Error("Word target was not found.");locate(b,e);return null;}catch(err){return err instanceof Error?err.message:String(err);}}
export async function applyEdits(snapshot:Snapshot,plan:Plan,selected:string[]){
 if(await sha256(snapshot.bytes)!==plan.source_sha256||snapshot.hash!==plan.source_sha256)throw Error("This edit plan belongs to a different original file.");
 resultSchema.parse({edits:plan.edits,warnings:plan.warnings});
 if(new Set(plan.edits.map(e=>e.id)).size!==plan.edits.length||new Set(selected).size!==selected.length||selected.some(id=>!plan.edits.some(e=>e.id===id)))throw Error("Invalid or duplicate edit IDs.");
 const groups=new Map<string,ReturnType<typeof locate>[]>();
 for(const e of plan.edits.filter(e=>selected.includes(e.id))){const b=snapshot.blocks.find(b=>b.id===e.block_id);if(!b)throw Error("Target paragraph missing.");const resolved=locate(b,e);groups.set(b.id,[...(groups.get(b.id)||[]),resolved]);}
 for(const items of groups.values()){items.sort((a,b)=>a.start-b.start);for(let i=1;i<items.length;i++){const a=items[i-1],b=items[i];if(b.start<a.end||b.start===a.start||(b.start===a.end&&(a.start===a.end||b.start===b.end)))throw Error("Selected edits overlap or share an insertion boundary.");}}
 const {zip,doc,paragraphs}=open(snapshot.bytes);
 for(const [id,items] of groups){const p=paragraphs[Number(id.split("p").pop())-1];for(const {start,end,edit} of items.reverse()){
  const nodes=textNodes(p);let offset=0,inserted=false;
  for(const n of nodes){const s=n.textContent??"",lo=offset,hi=offset+s.length;offset=hi;
   if(start===end){if(!inserted&&start>=lo&&start<=hi){n.textContent=s.slice(0,start-lo)+edit.new_text+s.slice(start-lo);n.setAttributeNS(XML,"xml:space","preserve");inserted=true;}continue;}
   if(hi<=start||lo>=end)continue;
   n.textContent=s.slice(0,Math.max(0,start-lo))+(!inserted?edit.new_text:"")+s.slice(Math.min(s.length,Math.max(0,end-lo)));n.setAttributeNS(XML,"xml:space","preserve");inserted=true;
  }
  if(!inserted)throw Error("No editable text run at the requested position.");
 }}
 zip["word/document.xml"]=strToU8(new XMLSerializer().serializeToString(doc));parseXml(strFromU8(zip["word/document.xml"]));
 return zipSync(zip,{level:6});
}
export function compareText(output:Snapshot,reference:Snapshot){
 if(output.hasRevisions||reference.hasRevisions)throw Error("Text comparison requires clean documents with tracked revisions accepted. This pilot cannot score tracked-change references reliably.");
 const a=output.blocks.filter(b=>b.text.trim()).map(b=>b.text),b=reference.blocks.filter(b=>b.text.trim()).map(b=>b.text);
 return{exact_body_text_match:JSON.stringify(a)===JSON.stringify(b),output_paragraphs:a.length,reference_paragraphs:b.length,scope:"Body and table text only. Not handwriting accuracy, layout, headers, formatting, or tracked-change correctness."};
}
