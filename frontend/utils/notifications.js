import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';

// --- Notification Handler (Required by Expo) ---
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

// --- Function to get the device's Push Token ---
export async function registerForPushNotificationsAsync() {
  let token;
  if (Device.isDevice) {
    const { status: existingStatus } = await Notifications.getPermissionsAsync();
    let finalStatus = existingStatus;
    if (existingStatus !== 'granted') {
      const { status } = await Notifications.requestPermissionsAsync();
      finalStatus = status;
    }
    if (finalStatus !== 'granted') {
      alert('Failed to get push token for push notification!');
      return;
    }
    try {
        token = await Notifications.getExpoPushTokenAsync({
            projectId: '7db31db4-91d2-4ca5-ab01-b02c50fa56a2',
        });
        console.log('Expo Push Token:', token.data);
    } catch (e) {
        console.error("Failed to get push token", e);
        alert("Failed to get push token. Make sure you've set your Expo Project ID in App.js.");
    }
  } else {
    console.log('Skipping push notifications on simulator.');
  }

  if (Platform.OS === 'android') {
    Notifications.setNotificationChannelAsync('default', {
      name: 'default',
      importance: Notifications.AndroidImportance.MAX,
      vibrationPattern: [0, 250, 250, 250],
      lightColor: '#FF231F7C',
    });
  }
  return token;
}
