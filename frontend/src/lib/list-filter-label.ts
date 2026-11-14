/** Keep SSR, mounted controls and retained loading frames on the same label policy. */
export function listFilterLabel(value:string,defaultValue:string,label:string,selectedLabel?:string|null,showDefaultValue=false) {
  return value===defaultValue&&!showDefaultValue?label:selectedLabel||label;
}
