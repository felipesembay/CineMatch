import {z} from 'zod';
import {mockRequest} from '../mocks/handlers';
import {auth} from './auth';
import {USER_ROUTE,userStub} from './userStub';
import {itemSummarySchema,itemDetailSchema,pageSchema,recommendationListSchema,genreSchema,preferencesSchema,favoritesSchema} from './types';
import type {Preferences,TrackingEvent} from './types';
export const API_BASE_URL=import.meta.env.VITE_API_BASE_URL||'http://127.0.0.1:8010';
export const USE_MOCKS=import.meta.env.VITE_USE_MOCKS!=='false';
// Rotas de usuário ainda sem backend (Fase 4): stub em memória com itens reais da API.
export const MOCK_USER_ENDPOINTS=import.meta.env.VITE_MOCK_USER_ENDPOINTS!=='false';
async function fetchJson(method:string,path:string,requestId:string,body?:unknown):Promise<unknown>{const response=await fetch(`${API_BASE_URL}${path}`,{method,credentials:'include',headers:{'Content-Type':'application/json','X-Request-ID':requestId},body:body===undefined?undefined:JSON.stringify(body)});if(!response.ok)throw Error(response.status===404?'Título não encontrado.':`Erro HTTP ${response.status}`);return response.status===204?{ok:true}:response.json();}
const okSchema=z.object({ok:z.boolean()}).passthrough();
async function request<T>(method:string,path:string,schema:z.ZodType<T>,body?:unknown,protectedRoute=false):Promise<T>{
 if(protectedRoute&&!auth.getUser())throw Error('Faça login para continuar.');
 const requestId=crypto.randomUUID();
 let raw:unknown;
 if(USE_MOCKS)raw=await mockRequest(method,path,body);
 else if(MOCK_USER_ENDPOINTS&&USER_ROUTE.test(path))raw=await userStub(method,path,body,p=>fetchJson('GET',p,crypto.randomUUID()));
 else raw=await fetchJson(method,path,requestId,body);
 return schema.parse(raw);
}
const qs=(params:Record<string,string|number|undefined>)=>{const u=new URLSearchParams();Object.entries(params).forEach(([k,v])=>{if(v!==undefined&&v!=='')u.set(k,String(v))});return u.toString()};
export type MovieFilters={q?:string;type?:string;genre?:string;year_from?:number;year_to?:number;sort?:string;page?:number;page_size?:number};
export const api={movies:(p:MovieFilters={})=>request('GET',`/movies?${qs(p)}`,pageSchema(itemSummarySchema)),genres:()=>request('GET','/genres',genreSchema),detail:(id:number)=>request('GET',`/movies/${id}`,itemDetailSchema),similar:(id:number,k=12)=>request('GET',`/movies/${id}/similar?k=${k}`,recommendationListSchema),recommendations:(k=20)=>request('GET',`/recommendations/me?k=${k}`,recommendationListSchema,undefined,true),preferences:()=>request('GET','/users/me/preferences',preferencesSchema,undefined,true),savePreferences:(p:Preferences)=>request('PUT','/users/me/preferences',preferencesSchema,p,true),favorites:()=>request('GET','/users/me/favorites',favoritesSchema,undefined,true),addFavorite:(id:number)=>request('PUT',`/users/me/favorites/${id}`,okSchema,undefined,true),removeFavorite:(id:number)=>request('DELETE',`/users/me/favorites/${id}`,okSchema,undefined,true),replaceFavorites:(ids:number[])=>request('PUT','/users/me/favorites',favoritesSchema,{ids},true),rate:(item_id:number,value:number)=>request('POST','/ratings',okSchema,{item_id,value},true),events:(events:TrackingEvent[])=>request('POST','/events',okSchema,{events})};
