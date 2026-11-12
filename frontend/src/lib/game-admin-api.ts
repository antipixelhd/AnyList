export class GameAdminError extends Error {constructor(message:string,public status:number){super(message);}}
export async function gameAdminRequest(path:string,token:string|undefined,body?:unknown){
  const response=await fetch(`http://127.0.0.1:${process.env.BACKEND_PORT??'7331'}/tracking/games${path}`,{
    method:body?'POST':'GET',headers:{...(token?{Authorization:`Bearer ${token}`} : {}),...(body?{'Content-Type':'application/json'}:{})},
    ...(body?{body:JSON.stringify(body)}:{}),cache:'no-store',
  });
  const value=await response.json().catch(()=>({}));
  if(!response.ok)throw new GameAdminError(typeof value.detail==='string'?value.detail:'Game metadata is temporarily unavailable.',response.status);
  return value;
}
