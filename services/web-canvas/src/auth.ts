export function bootstrapCapabilityToken(live: boolean, href: string, replace: (url: string) => void, retainedToken: string | null = null): string | null {
  const url = new URL(href);
  const bootToken = url.searchParams.get("token");
  if (bootToken) {
    url.searchParams.delete("token");
    replace(`${url.pathname}${url.search}${url.hash}`);
  }
  return live ? bootToken ?? retainedToken : null;
}
