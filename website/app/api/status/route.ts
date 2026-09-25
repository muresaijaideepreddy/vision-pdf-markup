import { env } from "cloudflare:workers";
import { getChatGPTUser } from "@/app/chatgpt-auth";
export async function GET(){
 const user=await getChatGPTUser();const config=env as unknown as Record<string,string|undefined>;
 const ready=Boolean(user&&config.OLLAMA_BASE_URL&&config.MARKUPDOC_MODEL);
 return Response.json({ready,provider:"Lab-hosted Ollama",requested_model:config.MARKUPDOC_MODEL||null,models:(config.MARKUPDOC_MODELS||config.MARKUPDOC_MODEL||"").split(",").filter(Boolean),reason:!user?"Sign in to this private site to run a model.":ready?null:"The model server has not been configured."},{headers:{"Cache-Control":"no-store"}});
}
