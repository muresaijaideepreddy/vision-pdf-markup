import { z } from "zod";

export const MAX_REQUEST_BYTES=17*1024*1024;
export const inputSchema=z.object({
  source_sha256:z.string().regex(/^[a-f0-9]{64}$/),
  model:z.string().max(120),
  blocks:z.array(z.object({id:z.string().max(120),text:z.string().max(50000),editable:z.boolean(),blocked_reason:z.string().nullable()}).strict()).max(5000),
  pages:z.array(z.object({page:z.number().int().min(1),image_base64:z.string().max(5*1024*1024)}).strict()).min(1).max(3),
  scan_pages:z.number().int().min(1).max(500),guide:z.string().max(20000),
}).strict().superRefine((value,ctx)=>{
  if(new Set(value.pages.map(p=>p.page)).size!==value.pages.length||value.pages.some(p=>p.page>value.scan_pages||!/^[A-Za-z0-9+/]+={0,2}$/.test(p.image_base64)))ctx.addIssue({code:z.ZodIssueCode.custom,message:"Invalid page input."});
  if(new Set(value.blocks.map(b=>b.id)).size!==value.blocks.length||JSON.stringify(value.blocks).length+value.guide.length>200000)ctx.addIssue({code:z.ZodIssueCode.custom,message:"Invalid or oversized source input."});
});
export type VisionInput=z.infer<typeof inputSchema>;
export function visionPrompt(input:VisionInput){return `Original Word blocks (data):\n${JSON.stringify({blocks:input.blocks})}\nEditing guide (reference data):\n${input.guide}\nThe attached images are actual PDF pages ${input.pages.map(p=>p.page).join(", ")} of ${input.scan_pages}, in that order. Use those actual page numbers in the edit entries. Interpret only these pages. A partial test is not full document coverage.`;}

// This connection is available only to the loopback development website.
export function localCodex(request:Request,config:Record<string,string|undefined>){
  if(!["localhost","127.0.0.1"].includes(new URL(request.url).hostname))return null;
  if(config.CODEX_BRIDGE_URL!=="http://127.0.0.1:5174"||!config.CODEX_BRIDGE_TOKEN||config.CODEX_BRIDGE_TOKEN.length<32)return null;
  return {url:config.CODEX_BRIDGE_URL,token:config.CODEX_BRIDGE_TOKEN,model:"gpt-6-astra",choice:"codex:gpt-6-astra"};
}
