export function nanoid(size = 12): string {
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789'
  let id = ''
  for (let i = 0; i < size; i++) id += chars[Math.floor(Math.random() * chars.length)]
  return id
}
