import {deviceStreamingCountry,streamingCountry} from './streaming-region.ts';

/** An automatic device hint; independent from date format and browse overrides. */
export const DEVICE_COUNTRY_COOKIE='anylist_device_country';
export function deviceStoreCountry(timeZone?:string,languages?:readonly string[]) {
  return deviceStreamingCountry(timeZone,languages)||'US';
}
export function rememberDeviceCountry(cookie=document.cookie,detected=deviceStoreCountry()) {
  const country=streamingCountry(detected)||'US';
  const saved=cookie.split(';').map(part=>part.trim()).find(part=>part.startsWith(`${DEVICE_COUNTRY_COOKIE}=`))?.slice(DEVICE_COUNTRY_COOKIE.length+1);
  if(saved!==country)document.cookie=`${DEVICE_COUNTRY_COOKIE}=${country}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol==='https:'?'; Secure':''}`;
  return country;
}
