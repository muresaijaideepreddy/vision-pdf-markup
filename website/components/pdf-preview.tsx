"use client";
import { useEffect, useState } from "react";
import { renderSelectedPages } from "@/lib/pdf-renderer";
import { Button } from "@/components/ui/button";

export function PdfPreview({file, initialPage}:{file:File;initialPage:number}) {
  const [page,setPage]=useState(initialPage);
  const [total,setTotal]=useState<number|null>(null);
  const [image,setImage]=useState("");
  const [error,setError]=useState("");
  useEffect(()=>{setPage(initialPage);setTotal(null);},[file,initialPage]);
  useEffect(()=>{
    let active=true;setImage("");setError("");
    void renderSelectedPages(file,page,page,()=>{}).then(result=>{
      if(active){setTotal(result.scan_pages);setImage(`data:image/jpeg;base64,${result.pages[0].image_base64}`);}
    }).catch(err=>{if(active)setError(err instanceof Error?err.message:String(err));});
    return()=>{active=false;};
  },[file,page]);
  return <div className="pdf-preview">
    <div className="preview-controls"><Button variant="outline" disabled={page<=1} onClick={()=>setPage(p=>p-1)}>Previous page</Button><span>Page {page}{total?` of ${total}`:""}</span><Button variant="outline" disabled={!total||page>=total} onClick={()=>setPage(p=>p+1)}>Next page</Button></div>
    {error?<p role="alert" className="error-box">PDF preview failed: {error}</p>:image?<img src={image} alt={`Marked scan page ${page}. This is the same rendering used for the vision model.`}/>:<p role="status">Rendering scan page {page}…</p>}
    <p className="preview-caption">Preview uses the same page renderer as the model input. Browsing does not change the analysis range.</p>
  </div>;
}
