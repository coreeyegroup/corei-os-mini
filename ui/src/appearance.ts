export const themes = ['Standard', 'Black', 'Dark Blue', 'White', 'COREI Orbit'] as const;
export type Theme = typeof themes[number];
export type Appearance = { theme: Theme; fontSize: number; density: 'Compact' | 'Comfortable'; activity: boolean; status: boolean; wrapTabs: boolean };
export const appearanceKey = 'corei-os-mini.appearance.v1';
export const defaultAppearance: Appearance = {theme:'Standard',fontSize:12,density:'Compact',activity:true,status:true,wrapTabs:false};
export function restoreAppearance(raw:string|null):Appearance {
  try {
    const x=JSON.parse(raw||'null');
    if(!x || !themes.includes(x.theme) || !Number.isInteger(x.fontSize) || x.fontSize<11 || x.fontSize>16 || !['Compact','Comfortable'].includes(x.density) || ['activity','status','wrapTabs'].some(k=>typeof x[k]!=='boolean')) return {...defaultAppearance};
    return {theme:x.theme,fontSize:x.fontSize,density:x.density,activity:x.activity,status:x.status,wrapTabs:x.wrapTabs};
  } catch {return {...defaultAppearance};}
}
