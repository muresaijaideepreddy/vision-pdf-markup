import { env } from "cloudflare:workers";
import { getChatGPTUser } from "@/app/chatgpt-auth";
import { localCodex } from "@/lib/vision-input";
export async function GET(request:Request){
 const user=await getChatGPTUser(),config=env as unknown as Record<string,string|undefined>;
 const models=config.OLLAMA_BASE_URL?(config.MARKUPDOC_MODELS||config.MARKUPDOC_MODEL||"").split(",").filter(Boolean):[];
 const codex=localCodex(request,config);let codexReady=false,pdfReady=false;
 if(user&&codex){try{const r=await fetch(codex.url+"/health",{headers:{Authorization:`Bearer ${codex.token}`},redirect:"manual",signal:AbortSignal.timeout(2000)});if(r.ok){const status=await r.json() as {ready?:boolean;pdf_ready?:boolean};codexReady=status.ready===true;pdfReady=status.pdf_ready===true;}}catch{/* Ollama stays available while the helper is offline. */}}
 if(codexReady&&codex)models.unshift(codex.choice);
 const ready=Boolean(user&&models.length);
 return Response.json({ready,pdf_ready:pdfReady,pdf_reason:pdfReady?null:"PDF export requires the local adapter and Microsoft Word on Windows. Word download is still available.",provider:codexReady?"Codex CLI (ChatGPT sign-in) and lab Ollama":"Lab-hosted Ollama",requested_model:codexReady?codex?.choice:config.MARKUPDOC_MODEL||null,models,codex_ready:codexReady,reason:!user?"Sign in to this private site to run a model.":ready?null:"No model connection is available."},{headers:{"Cache-Control":"no-store"}});
}
