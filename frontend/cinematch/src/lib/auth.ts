import {useSyncExternalStore} from 'react';
// Autenticação demonstrativa, apenas em memória. Substituir por sessão httpOnly do FastAPI.
type User={email:string;name:string;onboarded:boolean};
let user:User|null=null;
const listeners=new Set<()=>void>();
const notify=()=>listeners.forEach(fn=>fn());
export const auth={getUser:()=>user,subscribe:(fn:()=>void)=>{listeners.add(fn);return ()=>listeners.delete(fn)},login:(email:string,_password:string)=>{user={email,name:email.split('@')[0],onboarded:false};notify();return user},logout:()=>{user=null;notify()},completeOnboarding:()=>{if(user){user={...user,onboarded:true};notify()}}};
export const useAuth=()=>useSyncExternalStore(auth.subscribe,auth.getUser,auth.getUser);
