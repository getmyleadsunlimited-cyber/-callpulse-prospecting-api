type NavigationDestination = "home"|"account"|"help";
export type AnalyticsEvent = {name:"shell_loaded"}|{name:"workspace_switched"}|{name:"navigation_used";destination:NavigationDestination};
const forbiddenKeys=/email|name|phone|address|message|token|secret|lead|prospect/i;
export function track(event:AnalyticsEvent):void { if(Object.keys(event).some((key)=>forbiddenKeys.test(key))) throw new Error("PII fields are prohibited in analytics"); if(process.env.NODE_ENV==="development") console.info("analytics",event.name); }
