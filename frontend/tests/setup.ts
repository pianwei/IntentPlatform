import "@testing-library/jest-dom/vitest";

// jsdom does not implement native dialog methods; browser checks verify real focus behavior.
Object.defineProperty(HTMLDialogElement.prototype,"showModal",{configurable:true,writable:true,value(){this.setAttribute("open","");}});
Object.defineProperty(HTMLDialogElement.prototype,"close",{configurable:true,writable:true,value(){this.removeAttribute("open");}});
