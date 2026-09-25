// Local experiment only. Never expose this server through a tunnel or public host.
import http from "node:http";
import { spawn, execFile } from "node:child_process";
import { readFile, writeFile, mkdtemp, mkdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseEnv } from "node:util";
import { timingSafeEqual } from "node:crypto";
import { inputSchema, MAX_REQUEST_BYTES, visionPrompt } from "../lib/vision-input.ts";
import { SYSTEM_PROMPT, JSON_SCHEMA, PROMPT_VERSION } from "../lib/vision-contract.ts";
import { resultSchema } from "../lib/document-engine.ts";
import {pdfAvailable,exportPdf,MAX_DOCX_BYTES} from './pdf-export.mjs';

const project=path.resolve(fileURLToPath(new URL("../",import.meta.url)));
const config={...parseEnv(await readFile(path.join(project,".dev.vars"),"utf8")),...process.env};
const token=config.CODEX_BRIDGE_TOKEN;
if(!token||token.length<32)throw Error("Configure CODEX_BRIDGE_TOKEN in the ignored .dev.vars file first.");
const executable=config.CODEX_CLI_PATH||"codex";
await new Promise((resolve,reject)=>execFile(executable,["login","status"],{windowsHide:true,timeout:10000},(err)=>err?reject(Error("Sign in with codex login before starting the local bridge.")):resolve()));
const model="gpt-6-astra",choice=`codex:${model}`,port=5174;
const pdfReady=await pdfAvailable();
const jobsRoot=path.join(project,".sites-runtime","codex-jobs");
await mkdir(jobsRoot,{recursive:true});
let activeChild=null,activeRun=null,busy=false,shuttingDown=false;
function json(res,status,value){if(res.destroyed)return;res.writeHead(status,{"Content-Type":"application/json","Cache-Control":"no-store"});res.end(JSON.stringify(value));}
function authorized(req){const value=Buffer.from(req.headers.authorization||"");const expected=Buffer.from(`Bearer ${token}`);return value.length===expected.length&&timingSafeEqual(value,expected);}
async function stopChild(child){if(!child?.pid||child.exitCode!==null)return;if(process.platform==="win32")await new Promise(resolve=>execFile("taskkill.exe",["/PID",String(child.pid),"/T","/F"],{windowsHide:true},()=>resolve()));else child.kill("SIGKILL");}
async function cleanJob(directory){const resolved=path.resolve(directory);if(path.dirname(resolved)!==jobsRoot||!path.basename(resolved).startsWith("job-"))throw Error("Invalid temporary job path.");await rm(resolved,{recursive:true,force:true});}
async function body(req){let bytes=0;const chunks=[];for await(const chunk of req){bytes+=chunk.length;if(bytes>MAX_REQUEST_BYTES)throw Error("Request exceeds size limit.");chunks.push(chunk);}return inputSchema.parse(JSON.parse(Buffer.concat(chunks).toString("utf8")));}
async function run(input,res){
 const directory=await mkdtemp(path.join(jobsRoot,"job-"));const started=performance.now();
 try{
  const schema=structuredClone(JSON_SCHEMA);schema.properties.edits.items.properties.block_id.enum=["",...input.blocks.map(b=>b.id)];
  const schemaPath=path.join(directory,"schema.json"),outputPath=path.join(directory,"result.json");
  await writeFile(schemaPath,JSON.stringify(schema));
  const images=[];for(const [i,page] of input.pages.entries()){
   const bytes=Buffer.from(page.image_base64,"base64");const jpeg=bytes[0]===255&&bytes[1]===216&&bytes[2]===255;const png=bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]));
   if(!jpeg&&!png)throw Error("Only rendered JPEG or PNG pages are supported.");
   const imagePath=path.join(directory,`page-${i+1}.${jpeg?"jpg":"png"}`);await writeFile(imagePath,bytes);images.push(imagePath);
  }
  const prompt=SYSTEM_PROMPT+"\n\nThis is a bounded vision extraction test. Do not use tools, read other files, browse, run commands, or modify files. All necessary source text and page images are attached. Return only the JSON result.\n"+visionPrompt(input);
  const args=["exec","--ignore-user-config","--ephemeral","--sandbox","read-only","--skip-git-repo-check","--color","never","--model",model,
    "-c","features.shell_tool=false","-c","features.unified_exec=false","-c","tools.view_image=false","-c",'web_search="disabled"',"-c","agents.enabled=false","-c","apps._default.enabled=false","-c","project_doc_max_bytes=0",
    "--disable","apps","--disable","multi_agent","--disable","multi_agent_v2","--disable","code_mode","--enable","skip_host_skill_discovery","--disable","plugins","--disable","remote_plugin","--disable","hooks","--disable","browser_use","--disable","computer_use","--disable","skill_search","--disable","skill_mcp_dependency_install","--disable","code_mode_host",
    "--output-schema",schemaPath,"--output-last-message",outputPath,...images.flatMap(p=>["--image",p]),"-"];
  const childEnv={...process.env};for(const key of Object.keys(childEnv)){if(/^CODEX_BRIDGE_|^OLLAMA_|^MARKUPDOC_|^OPENAI_API_KEY$/.test(key))delete childEnv[key];}
  let captured="",outputBytes=0,failure=null;
  if(res.destroyed||shuttingDown)throw Error("The request was cancelled.");
  await new Promise((resolve,reject)=>{
   const child=spawn(executable,args,{cwd:directory,shell:false,windowsHide:true,env:childEnv,stdio:["pipe","pipe","pipe"]});activeChild=child;
   const fail=reason=>{failure??=reason;void stopChild(child);};
   const timer=setTimeout(()=>fail("Codex exceeded the 240-second limit."),240000);
   const disconnect=()=>{if(!res.writableEnded)fail("The request was cancelled.");};res.once("close",disconnect);if(res.destroyed||shuttingDown)fail("The request was cancelled.");
   child.stderr.on("data",chunk=>{outputBytes+=chunk.length;if(outputBytes>2*1024*1024)fail("Codex output exceeded the limit.");else captured+=chunk.toString("utf8");});
   child.stdout.on("data",chunk=>{outputBytes+=chunk.length;if(outputBytes>2*1024*1024)fail("Codex output exceeded the limit.");});
   child.stdin.on("error",()=>{});
   const finish=()=>{clearTimeout(timer);res.removeListener("close",disconnect);if(activeChild===child)activeChild=null;};
   child.once("error",()=>{finish();reject(Error("Could not start Codex. Check CODEX_CLI_PATH and codex login status."));});
   child.once("close",code=>{finish();if(failure)reject(Error(failure));else if(code!==0)reject(Error("Codex did not complete. Check codex login status and account access."));else resolve();});
   child.stdin.end(prompt);
  });
  const raw=await readFile(outputPath,"utf8");if(Buffer.byteLength(raw)>1024*1024)throw Error("Codex result exceeded the limit.");
  const result=resultSchema.parse(JSON.parse(raw));
  if(new Set(result.edits.map(e=>e.id)).size!==result.edits.length||result.edits.some(e=>!input.pages.some(p=>p.page===e.page)))throw Error("Codex returned invalid edit IDs or pages.");
  const header=captured.split(/\r?\nuser\r?\n/)[0];const reported=header.match(/^model:\s*(\S+)/m)?.[1]||null;
  return {result,telemetry:{mode:"codex_cli",provider:"Codex CLI (ChatGPT sign-in)",requested_model:model,returned_model:null,cli_reported_model:reported,model_identity_source:reported?"Codex CLI startup report":"Codex CLI configuration",api_request_seconds:(performance.now()-started)/1000,prompt_version:PROMPT_VERSION,pages_processed:input.pages.map(p=>p.page),scan_pages:input.scan_pages,usage:null,recorded_at:new Date().toISOString()}};
 }finally{await cleanJob(directory);}
}
const server=http.createServer(async(req,res)=>{
 if(req.headers.host!==`127.0.0.1:${port}`||req.headers.origin||!authorized(req))return json(res,403,{error:"Local server authentication required."});
 if(req.method==="GET"&&req.url==="/health")return json(res,200,{ready:true,model,choice,pdf_ready:pdfReady,provider:"Codex CLI (ChatGPT sign-in)"});
 if(req.method==='POST'&&req.url==='/export-pdf'){
  if(!pdfReady)return json(res,503,{error:'Local PDF export requires Microsoft Word on Windows.'});
  if(shuttingDown||busy)return json(res,429,{error:'Another local request is running. Please retry when it finishes.'});
  if(req.headers['content-type']!=='application/vnd.openxmlformats-officedocument.wordprocessingml.document')return json(res,415,{error:'Use DOCX bytes.'});
  busy=true;
  try{let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;if(size>MAX_DOCX_BYTES)throw Error('Word file exceeds 20 MB.');chunks.push(chunk);}activeRun=exportPdf(Buffer.concat(chunks),res);const pdf=await activeRun;if(!res.destroyed){res.writeHead(200,{'Content-Type':'application/pdf','Cache-Control':'no-store'});res.end(pdf);}}
  catch(err){json(res,502,{error:err instanceof Error?err.message:'PDF conversion failed.'});}
  finally{busy=false;activeRun=null;}
  return;
 }
 if(req.method!=="POST"||req.url!=="/propose")return json(res,404,{error:"Unknown endpoint."});
 if(!req.headers["content-type"]?.startsWith("application/json"))return json(res,415,{error:"Use JSON."});
 if(shuttingDown)return json(res,503,{error:"The local adapter is shutting down."});
 if(busy)return json(res,429,{error:"A Codex request is already running. Wait for it to finish."});
 busy=true;
 try{const input=await body(req);if(input.model!==choice)return json(res,400,{error:"Model is not enabled."});if(shuttingDown||res.destroyed)return;activeRun=run(input,res);json(res,200,await activeRun);}
 catch(err){json(res,502,{error:err?.name==="ZodError"?"Invalid document request.":err instanceof Error?err.message:"Codex request failed."});}
 finally{busy=false;activeRun=null;}
});
server.requestTimeout=300000;
server.listen(port,"127.0.0.1",()=>console.log(`Local Codex bridge listening on 127.0.0.1:${port}; model ${model}.`));
for(const signal of ["SIGINT","SIGTERM"])process.on(signal,async()=>{shuttingDown=true;server.close();server.closeAllConnections();const pending=activeRun;await stopChild(activeChild);if(pending)await pending.catch(()=>{});process.exit(0);});
