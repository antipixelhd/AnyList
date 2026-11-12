export function pinnedRailTarget(origin:number,height:number,trackHeight:number,viewportHeight:number,previous:number) {
  const top=96,bottom=24,lower=Math.min(top,viewportHeight-bottom-height);
  const viewportTop=origin+previous;
  return Math.max(0,Math.min(trackHeight-height,previous+Math.max(0,lower-viewportTop)+Math.min(0,top-viewportTop)));
}
