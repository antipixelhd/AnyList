import type {APIRoute} from 'astro';

// Bound work and logs. Never retain document URLs, samples, referrers or credentials.
let windowStarted=0, reports=0;
export const POST:APIRoute=async({request})=>{
  const now=Date.now();if(now-windowStarted>60_000){windowStarted=now;reports=0;}
  if(++reports>60)return new Response(null,{status:429});
  if(Number(request.headers.get('Content-Length')||0)>16_384)return new Response(null,{status:413});
  const reader=request.body?.getReader();let length=0;const chunks:Uint8Array[]=[];
  if(reader)while(true){const part=await reader.read();if(part.done)break;length+=part.value.length;if(length>16_384){await reader.cancel();return new Response(null,{status:413});}chunks.push(part.value);}
  try{
    const data=new Uint8Array(length);let offset=0;for(const chunk of chunks){data.set(chunk,offset);offset+=chunk.length;}
    const payload=JSON.parse(new TextDecoder().decode(data));
    for(const item of (Array.isArray(payload)?payload:[payload]).slice(0,10)){
      const body=item['csp-report']||item.body||{};
      const directive=body['effective-directive']||body.effectiveDirective;
      if(['script-src-elem','script-src-attr','script-src','object-src','base-uri','frame-ancestors'].includes(directive))console.info('CSP report-only',directive);
    }
  }catch{return new Response(null,{status:400});}
  return new Response(null,{status:204,headers:{'Cache-Control':'no-store'}});
};
