import {spawn,execFile} from 'node:child_process';
import {readFile,writeFile,mkdtemp,mkdir,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {validateRenderPackage} from '../lib/document-engine.ts';

export const MAX_DOCX_BYTES=20*1024*1024;
const project=path.resolve(fileURLToPath(new URL('../',import.meta.url)));
const jobsRoot=path.join(project,'.sites-runtime','pdf-jobs');
const script=path.join(project,'scripts','export-word-pdf.ps1');
export async function pdfAvailable(){
 if(process.platform!=='win32')return false;
 return new Promise(resolve=>execFile('powershell.exe',['-NoProfile','-NonInteractive','-Command',"if([type]::GetTypeFromProgID('Word.Application')){exit 0}else{exit 1}"],{windowsHide:true,timeout:10000},err=>resolve(!err)));
}
async function kill(pid){if(Number.isSafeInteger(pid)&&pid>0)await new Promise(resolve=>execFile('taskkill.exe',['/PID',String(pid),'/T','/F'],{windowsHide:true},()=>resolve()));}
export async function exportPdf(bytes,res){
 validateRenderPackage(new Uint8Array(bytes));
 await mkdir(jobsRoot,{recursive:true});const directory=await mkdtemp(path.join(jobsRoot,'job-'));
 try{
  const input=path.join(directory,'updated.docx'),output=path.join(directory,'updated.pdf');await writeFile(input,bytes);
  if(res.destroyed)throw Error('PDF request cancelled.');
  await new Promise((resolve,reject)=>{
   let ownedWordPid=null,stdout='',failure=null;
   const child=spawn('powershell.exe',['-NoProfile','-NonInteractive','-Sta','-ExecutionPolicy','Bypass','-File',script,'-InputPath',input,'-OutputPath',output],{windowsHide:true,shell:false,stdio:['ignore','pipe','pipe']});
   const fail=message=>{if(failure)return;failure=message;void kill(ownedWordPid).finally(()=>kill(child.pid));};
   const timer=setTimeout(()=>fail('PDF conversion exceeded two minutes. Word output is still available.'),120000);
   const disconnect=()=>{if(!res.writableEnded)fail('PDF request cancelled.');};res.once('close',disconnect);if(res.destroyed)disconnect();
   child.stdout.on('data',chunk=>{stdout+=chunk.toString();if(stdout.length>4000)return fail('Unexpected PDF converter output.');const match=stdout.match(/^WORD_PROCESS_ID=(\d+)\r?$/m);if(match)ownedWordPid=Number(match[1]);});
   child.stderr.resume();
   function finish(){clearTimeout(timer);res.removeListener('close',disconnect);}
   child.once('error',()=>{finish();reject(Error('Could not start the local Word PDF converter.'));});
   child.once('close',code=>{finish();if(failure)reject(Error(failure));else if(code!==0)reject(Error('Microsoft Word could not convert this document to PDF. Download the updated Word file and inspect it.'));else resolve();});
  });
  const pdf=await readFile(output);if(pdf.length>40*1024*1024||pdf.subarray(0,5).toString()!=='%PDF-')throw Error('The converter did not return a valid PDF.');return pdf;
 }finally{const resolved=path.resolve(directory);if(path.dirname(resolved)!==jobsRoot||!path.basename(resolved).startsWith('job-'))throw Error('Invalid PDF job path.');await rm(resolved,{recursive:true,force:true});}
}
