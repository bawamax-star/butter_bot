#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Script:
    join_captcha_bot.py
Description:
    Telegram Bot that send a captcha for each new user who join a group, and ban them if they 
    can not solve the captcha in a specified time. This is an approach to deny access to groups of 
    non-humans "users".
Author:
    Jose Rios Rubio
Creation date:
    09/09/2018
Last modified date:
    11/09/2018
Version:
    0.9.0
'''

####################################################################################################

### Imported modules ###
from sys import exit
from signal import signal, SIGTERM, SIGINT
from os import path, remove, makedirs, listdir
from shutil import rmtree
from datetime import datetime, timedelta
from time import time, sleep, strptime, mktime, strftime
from threading import Thread, Lock
from operator import itemgetter
from collections import OrderedDict
from telegram import MessageEntity, ParseMode, InputMediaPhoto,  InlineKeyboardButton, \
                     InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, RegexHandler, \
                         ConversationHandler, CallbackQueryHandler
from captcha.image import ImageCaptcha
from random import randint

from constants import CONST, TEXT
from tsjson import TSjson
from img_captcha_gen import CaptchaGenerator

####################################################################################################

### Globals ###
files_config_list = []
to_delete_in_time_messages_list = []
to_delete_join_messages_list = []
users_join_messages_list = []
new_users_list = []

# Create Captcha Generator object of specified size
CaptchaGen = CaptchaGenerator((160, 160))

####################################################################################################

### Termination signals handler for program process ###
def signal_handler(signal, frame):
    '''Termination signals (SIGINT, SIGTERM) handler for program process'''
    # Acquire all messages and users files mutex to ensure not read/write operation on them
    for chat_config_file in files_config_list:
        chat_config_file["File"].lock.acquire()
    # Close the program
    exit(0)


### Signals attachment ###
signal(SIGTERM, signal_handler) # SIGTERM (kill pid) to signal_handler
signal(SIGINT, signal_handler)  # SIGINT (Ctrl+C) to signal_handler

####################################################################################################

### General functions ###

def initialize_resources():
    '''Initialize resources by populating files list with chats found files'''
    # Remove old captcha directory and create it again
    if path.exists(CONST["CAPTCHAS_DIR"]):
        rmtree(CONST["CAPTCHAS_DIR"])
    makedirs(CONST["CAPTCHAS_DIR"])
    # Create data directory if it does not exists
    if not path.exists(CONST["CHATS_DIR"]):
        makedirs(CONST["CHATS_DIR"])
    else:
        # If chats directory exists, check all subdirectories names (chats ID)
        files = listdir(CONST["CHATS_DIR"])
        if files:
            for f_chat_id in files:
                # Populate config files list
                file_path = "{}/{}/{}".format(CONST["CHATS_DIR"], f_chat_id, CONST["F_CONF"])
                files_config_list.append(OrderedDict([("ID", f_chat_id), \
                    ("File", TSjson(file_path))]))
                # Create default configuration file if it does not exists
                if not path.exists(file_path):
                    default_conf = get_default_config_data()
                    for key, value in default_conf.items():
                        save_config_property(f_chat_id, key, value)


def create_image_captcha(img_file_name):
    '''Generate an image captcha from pseudo numbers'''
    image_file_path = "{}/{}.png".format(CONST["CAPTCHAS_DIR"], img_file_name)
    # If it doesn't exists, create captchas folder to store generated captchas
    if not path.exists(CONST["CAPTCHAS_DIR"]):
        makedirs(CONST["CAPTCHAS_DIR"])
    else:
        # If the captcha file exists remove it
        if path.exists(image_file_path):
            remove(image_file_path)
    # Generate and save the captcha
    captcha = CaptchaGen.gen_captcha_image(True)
    image = captcha["image"]
    image.save(image_file_path, "png")
    # Return a dictionary with captcha file path and captcha resolve characters
    generated_captcha = {"image": "", "number": ""}
    generated_captcha["image"] = image_file_path
    generated_captcha["number"] = captcha["characters"]
    return generated_captcha


def is_int(s):
    '''Check if the string is an integer number'''
    try:
        int(s)
        return True
    except ValueError:
        return False

####################################################################################################

### JSON chat config file functions ###

def get_default_config_data():
    '''Get default config data structure'''
    config_data = OrderedDict( \
    [ \
        ("Title", CONST["INIT_TITLE"]), \
        ("Link", CONST["INIT_LINK"]), \
        ("Enabled", CONST["INIT_ENABLE"]), \
        ("Captcha_Time", CONST["INIT_CAPTCHA_TIME_MIN"]), \
        ("Language", CONST["INIT_LANG"])
    ])
    return config_data


def save_config_property(chat_id, property, value):
    '''Store actual chat configuration in file'''
    fjson_config = get_chat_config_file(chat_id)
    config_data = fjson_config.read()
    if not config_data:
        config_data = get_default_config_data()
    config_data[property] = value
    fjson_config.write(config_data)


def get_chat_config(chat_id, param):
    '''Get specific stored chat configuration property'''
    file = get_chat_config_file(chat_id)
    if file:
        config_data = file.read()
        if not config_data:
            config_data = get_default_config_data()
    else:
        config_data = get_default_config_data()
    return config_data[param]


def get_chat_config_file(chat_id):
    '''Determine chat config file from the list by ID. Get the file if exists or create it if not'''
    file = OrderedDict([("ID", chat_id), ("File", None)])
    found = False
    if files_config_list:
        for chat_file in files_config_list:
            if chat_file["ID"] == chat_id:
                file = chat_file
                found = True
                break
        if not found:
            chat_config_file_name = "{}/{}/{}".format(CONST["CHATS_DIR"], chat_id, CONST["F_CONF"])
            file["ID"] = chat_id
            file["File"] = TSjson(chat_config_file_name)
            files_config_list.append(file)
    else:
        chat_config_file_name = "{}/{}/{}".format(CONST["CHATS_DIR"], chat_id, CONST["F_CONF"])
        file["ID"] = chat_id
        file["File"] = TSjson(chat_config_file_name)
        files_config_list.append(file)
    return file["File"]

####################################################################################################

### Telegram Related Functions ###

def user_is_admin(bot, user_id, chat_id):
    '''Check if the specified user is an Administrator of a group given by IDs'''
    try:
        group_admins = bot.get_chat_administrators(chat_id)
    except:
        return None
    for admin in group_admins:
        if user_id == admin.user.id:
            return True
    return False


def tlg_send_selfdestruct_msg(bot, chat_id, message):
    '''tlg_send_selfdestruct_msg_in() with default delete time'''
    tlg_send_selfdestruct_msg_in(bot, chat_id, message, CONST["T_DEL_MSG"])


def tlg_msg_to_selfdestruct(message):
    '''tlg_msg_to_selfdestruct_in() with default delete time'''
    tlg_msg_to_selfdestruct_in(message, CONST["T_DEL_MSG"])


def tlg_send_selfdestruct_msg_in(bot, chat_id, message, time_delete_min):
    '''Send a telegram message that will be auto-delete in specified time'''
    # Send the message
    sent_msg = bot.send_message(chat_id, message)
    # If has been succesfully sent
    if sent_msg:
        tlg_msg_to_selfdestruct(sent_msg)


def tlg_msg_to_selfdestruct_in(message, time_delete_min):
    '''Add a telegram message to be auto-delete in specified time'''
    # Get sent message ID and calculate delete time
    chat_id = message.chat_id
    user_id = message.from_user.id
    msg_id = message.message_id
    destroy_time = time() + (time_delete_min*60)
    # Add sent message data to to-delete messages list
    sent_msg_data = OrderedDict([("Chat_id", None), ("User_id", None), ("Msg_id", None), \
                                ("delete_time", None)])
    sent_msg_data["Chat_id"] = chat_id
    sent_msg_data["User_id"] = user_id
    sent_msg_data["Msg_id"] = msg_id
    sent_msg_data["delete_time"] = destroy_time
    to_delete_in_time_messages_list.append(sent_msg_data)

####################################################################################################

### Received Telegram not-command messages handlers ###

def msg_new_user(bot, update):
    '''New member join the group event handler'''
    # Get message data
    chat_id = update.message.chat_id
    # Determine configured bot language in actual chat
    lang = get_chat_config(chat_id, "Language")
    # For each new user that join or has been added
    for join_user in update.message.new_chat_members:
        join_user_id = join_user.id
        join_user_name = join_user.name
        if not join_user_name:
            join_user_name = "{} {}".format(update.message.from_user.first_name, \
                update.message.from_user.last_name)
        # If the added user is myself (this Bot)
        if bot.id == join_user_id:
            # Get the language of the Telegram client software the Admin that has added the Bot has,
            # to assume this is the chat language and configure Bot language of this chat
            admin_language = update.message.from_user.language_code[0:2].upper()
            if admin_language not in TEXT:
                admin_language = "EN"
            save_config_property(chat_id, "Language", admin_language)
            # Get and save chat data
            chat_title = update.message.chat.title
            if chat_title:
                save_config_property(chat_id, "Title", chat_title)
            chat_link = update.message.chat.username
            if chat_link:
                chat_link = "@{}".format(chat_link)
                save_config_property(chat_id, "Link", chat_link)
            # Send bot join message
            bot.send_message(chat_id, TEXT[lang]["START"])
        # The added user is not myself (this Bot)
        else:
            # If the captcha protection is enabled
            captcha_enable = get_chat_config(chat_id, "Enabled")
            if captcha_enable:
                # If the member that has been join the group is not a Bot
                if not update.message.new_chat_members[0].is_bot:
                    # Generate a pseudorandom captcha send it to telegram group and program message 
                    # selfdestruct
                    captcha = create_image_captcha(join_user_id)
                    captcha_timeout = get_chat_config(chat_id, "Captcha_Time")
                    img_caption = TEXT[lang]["NEW_USER_CAPTCHA_CAPTION"].format(join_user_name, \
                                                                                captcha_timeout)
                    # Prepare inline keyboard button to let user request another catcha
                    keyboard = [[InlineKeyboardButton("Other Captcha", callback_data=join_user_id)]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    # Image caption must be < 200 chars, so send separate image and text messages
                    #sent_img_msg = bot.send_photo(chat_id=chat_id, photo=open(captcha["image"], \
                    #                                 "rb"), caption=img_caption)
                    sent_img_msg = bot.send_photo(chat_id=chat_id, photo=open(captcha["image"], \
                                                    "rb"), reply_markup=reply_markup)
                    sent_msg = bot.send_message(chat_id, img_caption)
                    tlg_msg_to_selfdestruct_in(sent_img_msg, captcha_timeout+0.5)
                    tlg_msg_to_selfdestruct_in(sent_msg, captcha_timeout+0.5)
                    # Add join messages to delete
                    msg = \
                    {
                        "chat_id": chat_id,
                        "user_id" : join_user_id,
                        "msg_id_join0": update.message.message_id,
                        "msg_id_join1": sent_img_msg.message_id,
                        "msg_id_join2": sent_msg.message_id
                    }
                    to_delete_join_messages_list.append(msg)
                    # Add new user data to lists
                    new_user = \
                    {
                        "chat_id": chat_id,
                        "user_id" : join_user_id,
                        "user_name": join_user_name,
                        "captcha_num" : captcha["number"],
                        "join_time" : time()
                    }
                    new_users_list.append(new_user)


def msg_nocmd(bot, update):
    '''All Not-command messages handler'''
    # Get message data
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    user_id = update.message.from_user.id
    text = update.message.text
    # Verify if we are in a group and the captcha protection is enabled
    if chat_type != "private":
        captcha_enable = get_chat_config(chat_id, "Enabled")
        if captcha_enable:
            # Determine configured bot language in actual chat
            lang = get_chat_config(chat_id, "Language")
            # Search if this user is a new user that has not completed the captcha
            for new_user in new_users_list:
                if new_user["user_id"] == user_id:
                    if new_user["captcha_num"] in text:
                        # Remove join messages
                        for msg in to_delete_join_messages_list:
                            if msg["chat_id"] == chat_id:
                                if msg["user_id"] == user_id:
                                    try:
                                        # Uncomment next line to remove "user join" message too
                                        #bot.delete_message(msg["chat_id"], msg["msg_id_join0"])
                                        bot.delete_message(msg["chat_id"], msg["msg_id_join1"])
                                        bot.delete_message(msg["chat_id"], msg["msg_id_join2"])
                                    except:
                                        pass
                                    to_delete_join_messages_list.remove(msg)
                        # Remove user captcha numbers message
                        try:
                            bot.delete_message(chat_id, update.message.message_id)
                        except:
                            pass
                        # Send captcha solved message and program user and bot messages 
                        # selfdestruct in 1min
                        bot_msg = TEXT[lang]["CAPTHA_SOLVED"].format(new_user["user_name"])
                        tlg_msg_to_selfdestruct_in(update.message, 1)
                        tlg_send_selfdestruct_msg_in(bot, chat_id, bot_msg, 1)
                        new_users_list.remove(new_user)


def button_request_captcha(bot, update):
    '''Button "Other Captcha" pressed handler'''
    query = update.callback_query
    # If the query come from the expected user (the query data is the user ID of )
    if query.data == str(query.from_user.id):
        # Get query data
        chat_id = query.message.chat_id
        usr_id = query.from_user.id
        message_id = query.message.message_id
        # Search if this user is a new user that has not completed the captcha
        for i in range(len(new_users_list)):
            new_user = new_users_list[i]
            if new_user["user_id"] == usr_id:
                if new_user["chat_id"] == chat_id:
                    # Prepare inline keyboard button to let user request another catcha
                    keyboard = [[InlineKeyboardButton("Other Captcha", \
                                 callback_data=str(query.from_user.id))]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    # Generate a new captcha and edit previous captcha image message with this one
                    captcha = create_image_captcha(usr_id)
                    bot.edit_message_media(chat_id, message_id, media=InputMediaPhoto( \
                                           media=open(captcha["image"], "rb")), \
                                           reply_markup=reply_markup)
                    # Set and modified to new expected captcha number
                    new_user["captcha_num"] = captcha["number"]
                    new_users_list[i] = new_user
    bot.answer_callback_query(query.id)

####################################################################################################

### Received Telegram command messages handlers ###

def cmd_start(bot, update):
    '''Command /start message handler'''
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    lang = get_chat_config(chat_id, "Language")
    if chat_type == "private":
        bot.send_message(chat_id, TEXT[lang]["START"])
    else:
        tlg_msg_to_selfdestruct(update.message)
        tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["START"])


def cmd_help(bot, update):
    '''Command /help message handler'''
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    lang = get_chat_config(chat_id, "Language")
    bot_msg = TEXT[lang]["HELP"]
    if chat_type == "private":
        bot.send_message(chat_id, bot_msg)
    else:
        tlg_msg_to_selfdestruct(update.message)
        tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)


def cmd_commands(bot, update):
    '''Command /commands message handler'''
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    lang = get_chat_config(chat_id, "Language")
    if chat_type == "private":
        bot.send_message(chat_id, TEXT[lang]["COMMANDS"])
    else:
        tlg_msg_to_selfdestruct(update.message)
        tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["COMMANDS"])


def cmd_language(bot, update, args):
    '''Command /language message handler'''
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    chat_type = update.message.chat.type
    lang = get_chat_config(chat_id, "Language")
    allow_command = True
    if chat_type != "private":
        is_admin = user_is_admin(bot, user_id, chat_id)
        if is_admin == False:
            allow_command = False
    if allow_command:
        if len(args) == 1:
            lang_provided = args[0].upper()
            if lang_provided in TEXT:
                if lang_provided != lang:
                    lang = lang_provided
                    save_config_property(chat_id, "Language", lang)
                    bot_msg = TEXT[lang]["LANG_CHANGE"]
                else:
                    bot_msg = TEXT[lang]["LANG_SAME"]
            else:
                bot_msg = TEXT[lang]["LANG_BAD_LANG"]
        else:
            bot_msg = TEXT[lang]["LANG_NOT_ARG"]
    elif is_admin == False:
        bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
    else:
        bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
    if chat_type == "private":
        bot.send_message(chat_id, bot_msg)
    else:
        tlg_msg_to_selfdestruct(update.message)
        tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)


def cmd_time(bot, update, args):
    '''Command /time message handler'''
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    chat_type = update.message.chat.type
    lang = get_chat_config(chat_id, "Language")
    allow_command = True
    if chat_type != "private":
        is_admin = user_is_admin(bot, user_id, chat_id)
        if is_admin == False:
            allow_command = False
    if allow_command:
        if len(args) == 1:
            if is_int(args[0]):
                new_time = args[0]
                save_config_property(chat_id, "Captcha_Time", new_time)
                bot_msg = TEXT[lang]["TIME_CHANGE"].format(new_time)
            else:
                bot_msg = TEXT[lang]["TIME_NOT_NUM"]
        else:
            bot_msg = TEXT[lang]["TIME_NOT_ARG"]
    elif is_admin == False:
        bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
    else:
        bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
    if chat_type == "private":
        bot.send_message(chat_id, bot_msg)
    else:
        tlg_msg_to_selfdestruct(update.message)
        tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)


def cmd_enable(bot, update):
    '''Command /enable message handler'''
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    chat_type = update.message.chat.type
    lang = get_chat_config(chat_id, "Language")
    enable = get_chat_config(chat_id, "Enabled")
    is_admin = user_is_admin(bot, user_id, chat_id)
    if is_admin == True:
        if enable:
            bot_msg = TEXT[lang]["ALREADY_ENABLE"]
        else:
            enable = True
            save_config_property(chat_id, "Enabled", enable)
            bot_msg = TEXT[lang]["ENABLE"]
    elif is_admin == False:
        bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
    else:
        bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
    if chat_type == "private":
        bot.send_message(chat_id, bot_msg)
    else:
        tlg_msg_to_selfdestruct(update.message)
        tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)


def cmd_disable(bot, update):
    '''Command /disable message handler'''
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    chat_type = update.message.chat.type
    lang = get_chat_config(chat_id, "Language")
    enable = get_chat_config(chat_id, "Enabled")
    is_admin = user_is_admin(bot, user_id, chat_id)
    if is_admin == True:
        if enable:
            enable = False
            save_config_property(chat_id, "Enabled", enable)
            bot_msg = TEXT[lang]["DISABLE"]
        else:
            bot_msg = TEXT[lang]["ALREADY_DISABLE"]
    elif is_admin == False:
        bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
    else:
        bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
    if chat_type == "private":
        bot.send_message(chat_id, bot_msg)
    else:
        tlg_msg_to_selfdestruct(update.message)
        tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)


def cmd_version(bot, update):
    '''Command /version message handler'''
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    lang = get_chat_config(chat_id, "Language")
    bot_msg = TEXT[lang]["VERSION"].format(CONST["VERSION"])
    if chat_type == "private":
        bot.send_message(chat_id, bot_msg)
    else:
        tlg_msg_to_selfdestruct(update.message)
        tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)


def cmd_about(bot, update):
    '''Command /about handler'''
    chat_id = update.message.chat_id
    lang = get_chat_config(chat_id, "Language")
    bot_msg = TEXT[lang]["ABOUT_MSG"].format(CONST["DEVELOPER"], CONST["REPOSITORY"], \
        CONST["DEV_PAYPAL"], CONST["DEV_BTC"])
    bot.send_message(chat_id, bot_msg)


def cmd_captcha(bot, update):
    '''Command /captcha handler'''
    chat_id = update.message.chat_id
    captcha = create_image_captcha(chat_id)
    sent_img_msg = bot.send_photo(chat_id=chat_id, photo=open(captcha["image"], "rb"))
    tlg_msg_to_selfdestruct_in(sent_img_msg, 1)


####################################################################################################

### Main Loop Functions ###

def selfdestruct_messages(bot):
    '''Handle remove messages sent by the Bot with the timed self-delete function'''
    # Check each Bot sent message
    for sent_msg in to_delete_in_time_messages_list:
        # If actual time is equal or more than the expected sent msg delete time
        if time() >= sent_msg["delete_time"]:
            try:
                if bot.delete_message(sent_msg["Chat_id"], sent_msg["Msg_id"]):
                    to_delete_in_time_messages_list.remove(sent_msg)
            except:
                to_delete_in_time_messages_list.remove(sent_msg)


def check_time_to_ban_not_verify_users(bot):
    '''Check if the time for ban new users that has not completed the captcha has arrived'''
    for new_user in new_users_list:
        # If the time for ban has arrived
        captcha_timeout = get_chat_config(new_user["chat_id"], "Captcha_Time")
        if time() >= new_user["join_time"] + captcha_timeout*60:
            lang = get_chat_config(new_user["chat_id"], "Language")
            try:
                print("time to kick")
                bot.kickChatMember(new_user["chat_id"], new_user["user_id"])
                bot.unbanChatMember(new_user["chat_id"], new_user["user_id"])
                bot_msg = TEXT[lang]["NEW_USER_BAN"].format(new_user["user_name"])
                # Remove join messages
                for msg in to_delete_join_messages_list:
                    if msg["chat_id"] == new_user["chat_id"]:
                        if msg["user_id"] == new_user["user_id"]:
                            try:
                                # Uncomment next line to remove "user join" message too
                                #bot.delete_message(msg["chat_id"], msg["msg_id_join0"])
                                bot.delete_message(msg["chat_id"], msg["msg_id_join1"])
                                bot.delete_message(msg["chat_id"], msg["msg_id_join2"])
                            except:
                                pass
                            to_delete_join_messages_list.remove(msg)
                # Remove the ban user from the list
                new_users_list.remove(new_user)
            except Exception as e:
                print("except")
                if str(e) == "Not enough rights to restrict/unrestrict chat member":
                    print("Not enough rights to restrict/unrestrict chat member")
                    bot_msg = TEXT[lang]['NEW_USER_BAN_NO_RIGHTS'].format(new_user["user_name"])
                    # Update the ban time of the user to try again later
                    new_user["join_time"] = time()
                    new_users_list.remove(new_user)
                    new_users_list.append(new_user)
            print("Send selfdestruct")
            tlg_send_selfdestruct_msg(bot, new_user["chat_id"], bot_msg)

####################################################################################################

### Main Function ###

def main():
    '''Main Function'''
    # Initialize resources by populating files list and configs with chats found files
    initialize_resources()
    # Create an event handler (updater) for a Bot with the given Token and get the dispatcher
    updater = Updater(CONST["TOKEN"])
    dp = updater.dispatcher
    # Set to dispatcher a not-command text messages handler
    dp.add_handler(MessageHandler(Filters.text, msg_nocmd))
    # Set to dispatcher a new member join the group and member left the group events handlers
    dp.add_handler(MessageHandler(Filters.status_update.new_chat_members, msg_new_user))
    # Set to dispatcher request new captcha button callback handler
    dp.add_handler(CallbackQueryHandler(button_request_captcha))
    # Set to dispatcher all expected commands messages handler
    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("help", cmd_help))
    dp.add_handler(CommandHandler("commands", cmd_commands))
    dp.add_handler(CommandHandler("language", cmd_language, pass_args=True))
    dp.add_handler(CommandHandler("time", cmd_time, pass_args=True))
    dp.add_handler(CommandHandler("enable", cmd_enable))
    dp.add_handler(CommandHandler("disable", cmd_disable))
    dp.add_handler(CommandHandler("version", cmd_version))
    dp.add_handler(CommandHandler("about", cmd_about))
    # Next /captcha cmd just for test (use it in release can be a potentially DoS vulnerability)
    #dp.add_handler(CommandHandler("captcha", cmd_captcha))
    # Launch the Bot ignoring pending messages (clean=True)
    updater.start_polling(clean=True)
    # Handle remove of sent messages and not verify new users ban (main loop)
    while True:
        # Handle self-messages delete
        selfdestruct_messages(updater.bot)
        # Check time for ban new users that has not completed the captcha
        check_time_to_ban_not_verify_users(updater.bot)
        # Wait 10s (release CPU usage)
        sleep(10)

if __name__ == "__main__":
    main()

### End Of Code ###
