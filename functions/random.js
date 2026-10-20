// Alias route: /random behaves exactly like /pic
import picHandler, { onRequest as picOnRequest } from './pic.js';

export async function onRequest(context) {
  return picOnRequest(context);
}

export default picHandler;
