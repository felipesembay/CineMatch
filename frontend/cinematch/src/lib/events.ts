import {api,API_BASE_URL,USE_MOCKS,MOCK_USER_ENDPOINTS} from './api';
import type {TrackingEvent,EventSurface,EventType} from './types';
const queue:TrackingEvent[]=[];
let flushing=false;
export function track(type:EventType,surface:EventSurface,extra:Partial<Omit<TrackingEvent,'type'|'surface'|'event_id'|'occurred_at'>>={}){queue.push({event_id:crypto.randomUUID(),type,surface,occurred_at:new Date().toISOString(),...extra});}
export async function flushEvents(){if(flushing||!queue.length)return;flushing=true;const batch=queue.splice(0);try{await api.events(batch)}catch{queue.unshift(...batch)}finally{flushing=false}}
if(typeof window!=='undefined'){window.setInterval(flushEvents,5000);window.addEventListener('pagehide',()=>{if(!queue.length)return;const batch=queue.splice(0);if(!USE_MOCKS&&!MOCK_USER_ENDPOINTS&&navigator.sendBeacon){const blob=new Blob([JSON.stringify({events:batch})],{type:'application/json'});if(!navigator.sendBeacon(`${API_BASE_URL}/events`,blob))queue.unshift(...batch)}else if(!USE_MOCKS&&!MOCK_USER_ENDPOINTS)queue.unshift(...batch)});}
