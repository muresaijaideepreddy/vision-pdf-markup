"use client";
import { useEffect, useState } from "react";
import { renderSelectedPages } from "@/lib/pdf-renderer";
import { Button } from "@/components/ui/button";

export function UpdatedPdfPreview({file}:{file:File}) {
  const [page,setPage]=useState(1);
  const [total,setTotal]=useState<number|null>(null);
  const [image,setImage]=useState("");
  const [error,setError]=useState("");
  useEffect(()=>{setPage(1);setTotal(null);},[file]);
  useEffect(()=>{
    let active=true;setImage("");setError("");
    void renderSelectedPages(file,page,page,()=>{}).then(result=>{
      if(active){setTotal(result.scan_pages);setImage(`data:image/jpeg;base64,${result.pages[0].image_base64}`);}
    }).catch(err=>{if(active)setError(err instanceof Error?err.message:String(err));});
    return()=>{active=false;};
  },[file,page]);
  return <div className="pdf-preview">
    <div className="preview-controls"><Button variant="outline" disabled={page<=1} onClick={()=>setPage(p=>p-1)}>Previous updated page</Button><span>Page {page}{total?` of ${total}`:""}</span><Button variant="outline" disabled={!total||page>=total} onClick={()=>setPage(p=>p+1)}>Next updated page</Button></div>
    {error?<p role="alert" className="error-box">Updated PDF preview failed: {error}. The file is still available to download above.</p>:image?<img src={image} alt={`Updated document page ${page}, rendered from the Word copy with the selected edits applied.`}/>:<p role="status">Rendering updated page {page}…</p>}
    <p className="preview-caption">Updated document rendered from the new Word copy. Review the layout before sharing it.</p>
  </div>;
}
