import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

@Injectable({
  providedIn: 'root'
})
export class PushService {

  constructor(private http: HttpClient) {}

  isSupported(): boolean {
    return 'Notification' in window && 'serviceWorker' in navigator && 'PushManager' in window;
  }

  async getPermission(): Promise<NotificationPermission> {
    return Notification.permission;
  }

  async subscribe(): Promise<{ success: boolean; message: string }> {
    if (!this.isSupported()) {
      return { success: false, message: 'Push notifications are not supported in this browser.' };
    }

    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      return { success: false, message: 'Notification permission was denied.' };
    }

    try {
      const reg = await navigator.serviceWorker.register('/push-sw.js');
      await navigator.serviceWorker.ready;

      const vapidRes = await firstValueFrom(
        this.http.get<{ publicKey: string }>('/api/push/vapid-public-key')
      );

      const subscription = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: this.urlBase64ToUint8Array(vapidRes.publicKey)
      });

      await firstValueFrom(
        this.http.post('/api/push/subscribe', { subscription })
      );

      return { success: true, message: 'Browser push notifications enabled successfully.' };
    } catch (err: any) {
      return { success: false, message: err?.message || 'Failed to enable push notifications.' };
    }
  }

  async unsubscribe(): Promise<void> {
    if (!this.isSupported()) return;

    try {
      const reg = await navigator.serviceWorker.getRegistration('/push-sw.js');
      if (reg) {
        const subscription = await reg.pushManager.getSubscription();
        if (subscription) {
          await firstValueFrom(this.http.delete('/api/push/unsubscribe'));
          await subscription.unsubscribe();
        }
        await reg.unregister();
      }
    } catch (err) {
      console.error('Error unsubscribing from push:', err);
    }
  }

  async isSubscribed(): Promise<boolean> {
    if (!this.isSupported()) return false;
    try {
      const reg = await navigator.serviceWorker.getRegistration('/push-sw.js');
      if (!reg) return false;
      const subscription = await reg.pushManager.getSubscription();
      return !!subscription;
    } catch {
      return false;
    }
  }

  private urlBase64ToUint8Array(base64String: string): Uint8Array {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  }
}
