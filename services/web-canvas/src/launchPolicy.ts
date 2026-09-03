export async function approveThenStart<T>(approve: () => Promise<void>, start: () => Promise<T>): Promise<T> {
  await approve();
  return start();
}
