import {env} from 'cloudflare:workers';
import {getChatGPTUser} from '@/app/chatgpt-auth';
import {localCodex} from '@/lib/vision-input';
const MAX=20*1024*1024;
const error=(message:string,status:number)=>Response.json({error:message},{status,headers:{'Cache-Control':'no-store'}});
export async function POST(request:Request){
 if(!await getChatGPTUser())return error('Sign in to this site first.',401);
 if(request.headers.get('origin')!==new URL(request.url).origin)return error('The request must come from this site.',403);
 const bridge=localCodex(request,env as unknown as Record<string,string|undefined>);
 if(!bridge)return error('PDF export is available in the local Windows app with Microsoft Word installed. Download Word here and use Save as PDF.',503);
 if(request.headers.get('content-type')!=='application/vnd.openxmlformats-officedocument.wordprocessingml.document')return error('Use DOCX bytes.',415);
 if(Number(request.headers.get('content-length'))>MAX)return error('Word file exceeds 20 MB.',413);
 let bytes:Uint8Array;
 try{const reader=request.body?.getReader();if(!reader)throw Error();let size=0;const chunks:Uint8Array[]=[];while(true){const r=await reader.read();if(r.done)break;size+=r.value.length;if(size>MAX){await reader.cancel();throw Error();}chunks.push(r.value);}bytes=new Uint8Array(size);let offset=0;for(const c of chunks){bytes.set(c,offset);offset+=c.length;}if(size<4)throw Error();}catch{return error('Invalid or oversized Word file.',400);}
 try{
  const response=await fetch(bridge.url+'/export-pdf',{method:'POST',headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',Authorization:`Bearer ${bridge.token}`},body:new Uint8Array(bytes),redirect:'manual',signal:AbortSignal.any([request.signal,AbortSignal.timeout(135000)])});
  if(!response.ok){const data=await response.json() as {error?:string};return error(data.error||'PDF conversion failed.',response.status===429?429:502);}
  if(!response.headers.get('content-type')?.startsWith('application/pdf'))throw Error();
  return new Response(response.body,{headers:{'Content-Type':'application/pdf','Content-Disposition':'attachment; filename="updated.pdf"','Cache-Control':'no-store'}});
 }catch{return error('The local PDF converter did not respond. Your updated Word file is still available.',502);}
}
