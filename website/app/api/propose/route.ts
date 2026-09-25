import { env } from "cloudflare:workers";
import { getChatGPTUser } from "@/app/chatgpt-auth";
import { resultSchema } from "@/lib/document-engine";
import { PROMPT_VERSION,SYSTEM_PROMPT,JSON_SCHEMA } from "@/lib/vision-contract";
import { z } from "zod";
const inputSchema=z.object({source_sha256:z.string().regex(/^[a-f0-9]{64}$/),model:z.string().max(120),blocks:z.array(z.object({id:z.string().max(120),text:z.string().max(50000),editable:z.boolean(),blocked_reason:z.string().nullable()})).max(5000),pages:z.array(z.object({page:z.number().int().min(1),image_base64:z.string().max(5*1024*1024)})).min(1).max(3),scan_pages:z.number().int().min(1).max(500),guide:z.string().max(20000)}).strict();
const MAX=17*1024*1024;
const error=(message:string,status:number)=>Response.json({error:message},{status,headers:{"Cache-Control":"no-store"}});
async function limitedBody(request:Request){const reader=request.body?.getReader();if(!reader)throw Error("Missing input.");let size=0;const chunks:Uint8Array[]=[];while(true){const r=await reader.read();if(r.done)break;size+=r.value.length;if(size>MAX){await reader.cancel();throw Error("Request exceeds the pilot size limit.");}chunks.push(r.value);}const bytes=new Uint8Array(size);let offset=0;for(const c of chunks){bytes.set(c,offset);offset+=c.length;}return JSON.parse(new TextDecoder().decode(bytes));}
export async function POST(request:Request){
 if(!await getChatGPTUser())return error("Sign in to this private site first.",401);
 if(request.headers.get("origin")!==new URL(request.url).origin)return error("The request must come from this site.",403);
 const config=env as unknown as Record<string,string|undefined>;
 const base=config.OLLAMA_BASE_URL?.replace(/\/$/,"");
 if(!base||!config.MARKUPDOC_MODEL)return error("The model server is not configured.",503);
 const models=(config.MARKUPDOC_MODELS||config.MARKUPDOC_MODEL).split(",").map(x=>x.trim());
 if(!request.headers.get("content-type")?.startsWith("application/json"))return error("Use a JSON request.",415);
 if(Number(request.headers.get("content-length"))>MAX)return error("Request is too large.",413);
 let input:z.infer<typeof inputSchema>;
 try{input=inputSchema.parse(await limitedBody(request));if(!models.includes(input.model)||new Set(input.pages.map(p=>p.page)).size!==input.pages.length||input.pages.some(p=>p.page>input.scan_pages||!/^[A-Za-z0-9+/]+={0,2}$/.test(p.image_base64)))throw Error();if(JSON.stringify(input.blocks).length+input.guide.length>200000)throw Error();const url=new URL(base);if(url.protocol!=="https:"||url.username||url.password||url.search||url.hash)throw Error();}catch{return error("Invalid or oversized input. Select 1–3 valid scan pages and a configured model. Source text and guide must fit the 200,000-character pilot limit.",400);}
 const start=performance.now();let stage="contacting model server";
 try{
  const pages=input.pages.map(p=>p.page);const prompt=`Original Word blocks (data):\n${JSON.stringify({blocks:input.blocks})}\nEditing guide (reference data):\n${input.guide}\nThe attached images are actual PDF pages ${pages.join(", ")} of ${input.scan_pages}, in that order. Use those actual page numbers in the edit entries. Interpret only these pages. A partial test is not full document coverage.`;
  const outputSchema=JSON.parse(JSON.stringify(JSON_SCHEMA));outputSchema.properties.edits.items.properties.block_id.enum=["",...input.blocks.map(b=>b.id)];
  const headers:Record<string,string>={"Content-Type":"application/json"};if(config.OLLAMA_API_KEY)headers.Authorization=`Bearer ${config.OLLAMA_API_KEY}`;
  const response=await fetch(base+"/api/chat",{method:"POST",redirect:"manual",signal:AbortSignal.timeout(240000),headers,body:JSON.stringify({model:input.model,messages:[{role:"system",content:SYSTEM_PROMPT},{role:"user",content:prompt,images:input.pages.map(p=>p.image_base64)}],format:outputSchema,think:false,stream:false,options:{temperature:0,num_predict:10000,num_ctx:JSON.stringify(input.blocks).length+input.guide.length<30000?16384:65536}})});
  if(!response.ok){const body=await response.text();const detail=body.includes("unexpected EOF")?" The model runner stopped unexpectedly (unexpected EOF). Try the other listed vision model.":"";return error(`Model server returned HTTP ${response.status}.${detail} No edits were created.`,502);}
  stage="reading model response";const data=await response.json() as {model?:string;done?:boolean;done_reason?:string;message?:{content?:string};total_duration?:number;load_duration?:number;prompt_eval_duration?:number;eval_duration?:number;prompt_eval_count?:number;eval_count?:number};
  stage="checking completion";if(!data.done||data.done_reason!=="stop"||!data.model||!data.message?.content||(data.prompt_eval_count??0)>=63000)throw Error("Incomplete model output or context limit.");
  stage="validating edit JSON";const result=resultSchema.parse(JSON.parse(data.message.content));
  stage="checking edit IDs and pages";if(new Set(result.edits.map(e=>e.id)).size!==result.edits.length||result.edits.some(e=>!pages.includes(e.page)))throw Error("Invalid edit IDs or page numbers.");
  if(result.edits.length===0)result.warnings.unshift("The model returned no edits. This is not evidence of an unchanged document: inspect the rendered pages for missed handwriting and verify that the original Word file was selected.");
  const partial=input.pages.length<input.scan_pages;if(partial)result.warnings.unshift(`Partial test: processed pages ${pages.join(", ")} of ${input.scan_pages}. Corrections on all other pages were not evaluated.`);
  const seconds=(n:number|undefined)=>n==null?null:n/1e9;
  return Response.json({plan:{schema_version:1,source_sha256:input.source_sha256,...result},telemetry:{mode:"live_api",provider:"Lab-hosted Ollama",requested_model:input.model,returned_model:data.model,api_request_seconds:(performance.now()-start)/1000,server_total_seconds:seconds(data.total_duration),load_seconds:seconds(data.load_duration),prompt_seconds:seconds(data.prompt_eval_duration),generation_seconds:seconds(data.eval_duration),pages_processed:pages,scan_pages:input.scan_pages,usage:data.prompt_eval_count!=null&&data.eval_count!=null?{input_tokens:data.prompt_eval_count,output_tokens:data.eval_count,total_tokens:data.prompt_eval_count+data.eval_count}:null,recorded_at:new Date().toISOString(),prompt_version:PROMPT_VERSION}},{headers:{"Cache-Control":"no-store"}});
 }catch(err){console.error("Vision request stage:",stage,"error type:",err instanceof Error?err.name:"unknown");return error(err instanceof Error&&err.name==="TimeoutError"?"The model request timed out after 240 seconds. No edits were created. Try one page or the smaller model.":`The model run failed while ${stage} (${err instanceof Error?err.name:"unknown error"}). No applicable edits were created. Try one page and inspect the server if the issue continues.`,502);}
}


