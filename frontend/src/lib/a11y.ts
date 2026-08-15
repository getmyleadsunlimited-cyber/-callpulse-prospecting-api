export function announce(message:string):void { const region=document.getElementById("app-announcer"); if(region) region.textContent=message; }
export const visuallyHiddenStyle={position:"absolute",width:"1px",height:"1px",padding:0,margin:"-1px",overflow:"hidden",clip:"rect(0, 0, 0, 0)",whiteSpace:"nowrap",border:0} as const;
