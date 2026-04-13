import React, { useEffect, useRef } from 'react';
import { Linking } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import * as Notifications from 'expo-notifications';

import { Api } from './api';
import { FiltersProvider } from './context/FiltersContext';
import { registerForPushNotificationsAsync } from './utils/notifications';
import FiltersEditorScreen from './screens/FiltersEditorScreen';
import FeedScreen from './screens/FeedScreen';
import FiltersListScreen from './screens/FiltersListScreen';
import FilterCarsScreen from './screens/FilterCarsScreen';

const Tab = createBottomTabNavigator();
const Stack = createNativeStackNavigator();

function FiltersStack() {
  return (
    <Stack.Navigator>
      <Stack.Screen name="FiltersList" component={FiltersListScreen} options={{ title: 'Filtreler' }} />
      <Stack.Screen name="FilterCars" component={FilterCarsScreen} options={({ route }) => ({ title: route.params?.name || 'İlanlar' })} />
    </Stack.Navigator>
  );
}

export default function App() {
    const notificationListener = useRef();
    const responseListener = useRef();

    useEffect(() => {
        // 1. Register for push notifications and send token to backend
        registerForPushNotificationsAsync().then(token => {
            if (token) {
                Api.registerPushToken({ token: token.data })
                .then(() => console.log('Successfully registered push token with backend.'))
                .catch(e => console.error('Failed to register push token:', e));
            }
        });

        // 2. This listener is fired whenever a notification is received while the app is foregrounded
        notificationListener.current = Notifications.addNotificationReceivedListener(notification => {
            console.log('Notification received:', notification);
        });

        // 3. This listener is fired whenever a user taps on or interacts with a notification
        responseListener.current = Notifications.addNotificationResponseReceivedListener(response => {
            console.log('Notification response:', response);
            const { url } = response.notification.request.content.data;
            if (url) {
                Linking.openURL(url);
            }
        });

        return () => {
            Notifications.removeNotificationSubscription(notificationListener.current);
            Notifications.removeNotificationSubscription(responseListener.current);
        };
    }, []);

    return (
        <NavigationContainer>
        <FiltersProvider>
            <Tab.Navigator>
            <Tab.Screen name="Filtre ekle" component={FiltersEditorScreen} />
            <Tab.Screen name="Yeni İlanlar" component={FeedScreen} />
            <Tab.Screen name="Filtreler" component={FiltersStack} options={{ headerShown: false }} />
            </Tab.Navigator>
        </FiltersProvider>
        </NavigationContainer>
    );
}
