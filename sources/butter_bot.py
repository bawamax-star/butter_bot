#!/usr/bin/env python3
# -*- coding: utf-8 -*-
####################################################################################################

### Imported modules ###
import re, math, traceback, os
from sys import exit
from signal import signal, SIGTERM, SIGINT
from os import path, remove, makedirs, listdir
from shutil import rmtree
from datetime import datetime, timedelta
from time import time, sleep, strptime, mktime, strftime
from threading import Thread, Lock, Timer
from operator import itemgetter
from collections import OrderedDict
from random import randint
from telegram import (Update, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup,
	ChatPermissions, ParseMode, ChatAction)
from telegram.ext import (CallbackContext, Updater, CommandHandler, MessageHandler, Filters, 
	CallbackQueryHandler, Defaults)

from constants import CONST, TEXT

try:
	from secrets import SECRETS
except Exception as e:
	print("Copy 'secrets.example.py' to 'secrets.py' and fill in information. After that start the bot again!")
	print("Exit.\n")
	exit(0)

from tsjson import TSjson
from lib.multicolor_captcha_generator.img_captcha_gen import CaptchaGenerator
from telegram.error import (TelegramError, Unauthorized, BadRequest, 
							TimedOut, ChatMigrated, NetworkError)

####################################################################################################

### Globals ###
files_config_list = []
to_delete_in_time_messages_list = []
to_delete_join_messages_list = []
new_users_list = []
FOREVER = 999999999999999999999

# Create Captcha Generator object of specified size (2 -> 640x360)
CaptchaGen = CaptchaGenerator(2)

####################################################################################################

### Termination signals handler for program process ###
def signal_handler(signal,  frame):
	'''Termination signals (SIGINT, SIGTERM) handler for program process'''
	printts("Termination signal received. Releasing resources (Waiting for files to be closed)")
	# Acquire all messages and users files mutex to ensure not read/write operation on them
	for chat_config_file in files_config_list:
		chat_config_file["File"].lock.acquire()
	printts("All resources successfully released.")
	# Close the program
	printts("Exit")
	exit(0)


### Signals attachment ###
signal(SIGTERM, signal_handler) # SIGTERM (kill pid) to signal_handler
signal(SIGINT, signal_handler)  # SIGINT (Ctrl+C) to signal_handler

####################################################################################################

### General functions ###

def initialize_resources():
	'''Initialize resources by populating files list with chats found files'''
	global files_config_list
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
				files_config_list.append(OrderedDict([("ID", f_chat_id),
					("File", TSjson(file_path))]))
				# Create default configuration file if it does not exists
				if not path.exists(file_path):
					default_conf = get_default_config_data()
					for key, value in default_conf.items():
						save_config_property(f_chat_id, key, value)
	# Load and generate URL detector regex from TLD list file
	actual_script_path = path.dirname(path.realpath(__file__))
	load_urls_regex("{}/{}".format(actual_script_path, CONST["F_TLDS"]))
	# Load all languages texts
	load_texts_languages()


def load_urls_regex(file_path):
	'''Load URL detection Regex from IANA TLD list text file.'''
	tlds_str = ""
	list_file_lines = []
	try:
		with open(file_path, "r") as f:
			for line in f:
				if line is None:
					continue
				if (line == "") or (line == "\r\n") or (line == "\r") or (line == "\n"):
					continue
				# Ignore lines that start with # (first header line of IANA TLD list file)
				if line[0] == "#":
					continue
				line = line.lower()
				line = line.replace("\r", "")
				line = line.replace("\n", "|")
				list_file_lines.append(line)
	except Exception as e:
		printts("Error opening file \"{}\". {}".format(file_path, str(e)))
	if len(list_file_lines) > 0:
		tlds_str = "".join(list_file_lines)
	CONST["REGEX_URLS"] = CONST["REGEX_URLS"].format(tlds_str)


def load_texts_languages():
	'''Load all texts from each language file.'''
	for lang_iso_code in TEXT:
		lang_file = "{}/{}.json".format(CONST["LANG_DIR"], lang_iso_code.lower())
		json_lang_file = TSjson(lang_file)
		json_lang_texts = json_lang_file.read()
		if (json_lang_texts is None) or (json_lang_texts == {}):
			printts("Error loading language \"{}\" from {}. Language file not found or bad JSON "
					"sintax.".format(lang_iso_code, lang_file))
			printts("Exit.\n")
			exit(0)
		TEXT[lang_iso_code] = json_lang_texts


def create_image_captcha(img_file_name, difficult_level, chars_mode):
	'''Generate an image captcha from pseudo numbers'''
	image_file_path = "{}/{}.png".format(CONST["CAPTCHAS_DIR"], img_file_name)
	# If it doesn't exists, create captchas folder to store generated captchas
	if not path.exists(CONST["CAPTCHAS_DIR"]):
		makedirs(CONST["CAPTCHAS_DIR"])
	else:
		# If the captcha file exists remove it
		if path.exists(image_file_path):
			remove(image_file_path)
	# Generate and save the captcha with a random captcha background mono-color or multi-color
	captcha = CaptchaGen.gen_captcha_image(difficult_level, chars_mode, bool(randint(0, 1)))
	image = captcha["image"]
	image.save(image_file_path, "png")
	# Return a dictionary with captcha file path and captcha resolve characters
	generated_captcha = {"image": "", "number": ""}
	generated_captcha["image"] = image_file_path
	generated_captcha["number"] = captcha["characters"]
	return generated_captcha


def update_to_delete_join_msg_id(msg_chat_id, msg_user_id, message_id_key, new_msg_id_value):
	'''Update the msg_id_value from his key of the to_delete_join_messages_list'''
	global to_delete_join_messages_list
	i = 0
	while i < len(to_delete_join_messages_list):
		msg = to_delete_join_messages_list[i]
		if (msg["user_id"] == msg_user_id) and (msg["chat_id"] == msg_chat_id):
			msg[message_id_key] = new_msg_id_value
			if msg in to_delete_join_messages_list:
				to_delete_join_messages_list.remove(msg)
			to_delete_join_messages_list.append(msg)
			break
		i = i + 1


def printts(to_print="", timestamp=True):
	'''printts with timestamp.'''
	print_without_ts = False
	# Normal print if timestamp is disabled
	if (not timestamp):
		print_without_ts = True
	else:
		# If to_print is text and not other thing
		if isinstance(to_print, str):
			# Normalize EOLs to new line
			to_print = to_print.replace("\r", "\n")
			# If no text provided or text just contain spaces or EOLs
			if to_print == "":
				print_without_ts = True
			elif (" " in to_print) and (len(set(to_print)) == 1):
				print_without_ts = True
			elif ("\n" in to_print) and (len(set(to_print)) == 1):
				print_without_ts = True
			else:
				# Normal print for all text start EOLs
				num_eol = -1
				for character in to_print:
					if character == '\n':
						print("")
						num_eol = num_eol + 1
					else:
						break
				# Remove all text start EOLs (if any)
				if num_eol != -1:
					to_print = to_print[num_eol+1:]
	if print_without_ts:
		print(to_print)
	else:
		# Get actual time and print with timestamp
		actual_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
		print("{}: {}".format(actual_date, to_print))


def is_int(s):
	'''Check if the string is an integer number'''
	try:
		int(s)
		return True
	except ValueError:
		return False


def add_lrm(str_to_modify):
	'''Add a Left to Right Mark (LRM) at provided string start'''
	barray = bytearray(b"\xe2\x80\x8e")
	if str_to_modify == None:
		return u''
	str_to_modify = str_to_modify.encode("utf-8")
	for b in str_to_modify:
		barray.append(b)
	str_to_modify = barray.decode("utf-8")
	return str_to_modify

def show_user_captcha(bot, chat_id,user_name, lang):
	captcha_level = CONST["INIT_CAPTCHA_TIME_MIN"]
	captcha_chars_mode = CONST["INIT_CAPTCHA_CHARS_MODE"]
	captcha_timeout = CONST["INIT_CAPTCHA_TIME_MIN"]
	captcha = create_image_captcha(str(chat_id), captcha_level, captcha_chars_mode)
	captcha_timeout = get_chat_config(chat_id, "Captcha_Time")
	img_caption = TEXT[lang]["USER_CAPTCHA"].format(user_name,
			str(captcha_timeout))
	# Prepare inline keyboard button to let user request another catcha
	keyboard = [[InlineKeyboardButton(TEXT[lang]["OTHER_CAPTCHA_BTN_TEXT"],
			callback_data=chat_id)]]
	reply_markup = InlineKeyboardMarkup(keyboard)
	send_problem = False
	printts("[{}] Sending captcha message: {}...".format(chat_id, captcha["number"]))
	try:
		# Note: Img caption must be <= 1024 chars
		sent_img_msg = bot.send_photo(chat_id=chat_id, photo=open(captcha["image"],"rb"),
				reply_markup=reply_markup, caption=img_caption, timeout=20)
	except Exception as e:
		printts("[{}] {}".format(chat_id, str(e)))
		if str(e) != "Timed out":
			send_problem = True
		else:
			printts("sent_img_msg: {}".format(sent_img_msg))
	# Remove sent captcha image file from file system
	save_config_property(chat_id,"User_Solve_Result",captcha["number"])
	if path.exists(captcha["image"]):
		remove(captcha["image"])
	if not send_problem:
		# Add sent image to self-destruct list
		if not tlg_msg_to_selfdestruct_in(sent_img_msg, captcha_timeout+0.5):
			printts("[{}] sent_img_msg does not have all expected attributes. "
					"Scheduled for deletion".format(chat_id))

def uniq(lst):
	last = object()
	for item in lst:
		if item == last:
			continue
		yield item
		last = item

def get_protected_list():
	protected_list = []
	group_id_list = []
	for group in files_config_list:
		if int(group["ID"]) not in group_id_list:
			enabled = get_chat_config(group["ID"],"Protected")
			allowed = get_chat_config(group["ID"], "Allowed")
			title = get_chat_config(group["ID"],"Title")
			current_user = get_chat_config(group["ID"],"Protection_Current_User")
			current_time = get_chat_config(group["ID"],"Protection_Current_Time")
			captcha_timeout = get_chat_config(group["ID"],"Captcha_Time")
			if enabled and allowed and title:
				if current_user > 1 and time() > current_time + (captcha_timeout * 60):
					save_config_property(group["ID"],"Protection_Current_User",0)
				group_id_list.append(int(group["ID"]))
				protected_list.append([InlineKeyboardButton(title,callback_data="p{}".format(group["ID"]))])
	return protected_list

def get_public_list():
	public_list = []
	group_id_list = []
	for group in files_config_list:
		if int(group["ID"]) not in group_id_list:
			enabled = get_chat_config(group["ID"],"Public_Notes")
			allowed = get_chat_config(group["ID"], "Allowed")
			title = get_chat_config(group["ID"],"Title")
			if enabled and allowed and title:
				group_id_list.append(int(group["ID"]))
				public_list.append([InlineKeyboardButton(title,callback_data="n{}".format(group["ID"]))])
	return public_list

def get_user_full_name(msg):
	first_name = getattr(msg.from_user, "first_name", "")
	last_name = getattr(msg.from_user, "last_name", "")
	if last_name == None or last_name == "None":
		last_name=""
	if len(last_name) == 0:
		name = first_name
	else:
		name = "{} {}".format(first_name,last_name)
	name_chars =''.join(e for e in name if e.isalnum())
	if len(name_chars) ==0:
		name = "'{}'".format(name)
	return name

def send_welcome_msg(bot,chat_id, update, print_id):
	valid = None
	msg = getattr(update, "message", None)
	if msg:
		tlg_msg_to_selfdestruct(msg)
		user_name = msg.from_user.username
		user_id = msg.from_user.id 
		user_full_name = "<a href='tg://user?id={}'>{}</a>".format(user_id,get_user_full_name(msg))
		user_link = "tg://user?id={}".format(user_id)
		group_name = get_chat_config(chat_id,"Title")
		welcome_msg = get_chat_config(chat_id, "Welcome_Msg").format(user_name,"{}".format(user_full_name), user_id,user_link,group_name)
		welcome_msg = welcome_msg.replace("<br>","\n").replace("<br/>","\n")
		if welcome_msg != "-":
			valid = bot.send_message(print_id, welcome_msg,parse_mode=ParseMode.HTML,disable_web_page_preview=True,disable_notification=True)
			valid_id = int(getattr(valid, "message_id", 0))
			if msg.chat.type != "private" and valid_id > 0 and get_chat_config(chat_id,"Delete_Welcome"):
				tlg_msg_to_selfdestruct(valid)
				old_message_ids = get_chat_config(chat_id,"Last_Welcome_Msg")
				for old_message_id in old_message_ids:
					try:
						bot.delete_message(chat_id, old_message_id)
					except Exception as f:
						pass
				save_config_property(chat_id,"Last_Welcome_Msg",[valid_id])
	return valid_id


def test_note(bot,update, print_id, note):
	try:
		valid = bot.send_message(print_id, note,parse_mode=ParseMode.HTML,disable_web_page_preview=True,disable_notification=True,reply_to_message_id=update.message.message_id)
		valid_id = int(getattr(valid, "message_id", 0))
		if valid_id > 0:
			bot.delete_message(print_id,valid_id)
			return True
	except Exception as e:
		pass
	return False
def kick_user(bot,chat_id,user_id,user_name):
	kick_result = tlg_kick_user(bot, chat_id, user_id)
	if kick_result == 1:
		# Kick success
		bot_msg = TEXT["EN"]["NEW_USER_KICK"].format(user_name)
		# Set to auto-remove the kick message too, after a while
		tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)
	else:
		# Kick fail
		printts("[{}] Unable to kick".format(chat_id))
		if kick_result == -1:
			# The user is not in the chat
			bot_msg = TEXT["EN"]['NEW_USER_KICK_NOT_IN_CHAT'].format(
					user_name)
			# Set to auto-remove the kick message too, after a while
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)
		elif kick_result == -2:
			# Bot has no privileges to ban
			bot_msg = TEXT["EN"]['NEW_USER_KICK_NOT_RIGHTS'].format(
					user_name)
			# Send no rights for kick message without auto-remove
			try:
				bot.send_message(chat_id, bot_msg)
			except Exception as e:
				printts("[{}] {}".format(chat_id, str(e)))
		else:
			# For other reason, the Bot can't ban
			bot_msg = TEXT["EN"]['BOT_CANT_KICK'].format(user_name)
			# Set to auto-remove the kick message too, after a while
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)

def handle_request(bot,chat_id,user_id,captcha_timeout, lang):
	link = revoke_group_link(bot,chat_id)
	if len(link) > 1:
		user_time = time()
		save_config_property(chat_id,"Protection_Current_User",user_id)
		save_config_property(chat_id,"Protection_Current_Time",user_time)
		return TEXT[lang]["PROTECTION_SEND_LINK"].format(link,captcha_timeout)
		#Timer(int(captcha_timeout), revoke_group_link_delayed, [bot,chat_id,user_id,user_time]).start()
	else:
		return TEXT[lang]["PROTECTION_NO_LINK"]
		

def revoke_group_link_delayed(bot,chat_id, expected_user, expected_time):
	if expected_user == get_chat_config(chat_id,"Protection_Current_User"):
		if expected_time == get_chat_config(chat_id,"Protection_Current_Time"):
			return bot.exportChatInviteLink(chat_id)

def revoke_group_link(bot,chat_id):
	current_hash = get_chat_config(chat_id, "Invite_Hash")
	current_hash_time = get_chat_config(chat_id,"Invite_Hash_time")
	if (len(current_hash)>1) and (int(time()) < int(current_hash_time +CONST["MAX_INVITE_LINK_AGE"])) and (tlg_check_invite_hash(current_hash)):
		return CONST["INVITE_LINK_PREFIX"].format(current_hash)
	else:
		try:
			invite_link = bot.exportChatInviteLink(chat_id)
			new_hash = invite_link.split("/")
			new_hash=new_hash[len(new_hash)-1]
			if tlg_check_invite_hash(new_hash):
				save_config_property(chat_id,"Invite_Hash",new_hash)
				save_config_property(chat_id,"Invite_Hash_time",time())
				return invite_link
			else:
				if tlg_check_invite_hash(current_hash):
					return CONST["INVITE_LINK_PREFIX"].format(current_hash)
		except BadRequest as e:
			pass#handle no rights to revoke link
	return ""

def request_group_link(bot,chat_id,user_id,lang,query_id):
	printts("[{}]: user {} requested group link".format(chat_id,user_id))
	current_user = get_chat_config(chat_id,"Protection_Current_User")
	current_user_time = get_chat_config(chat_id,"Protection_Current_Time")
	captcha_timeout = get_chat_config(chat_id,"Captcha_Time")
	if ((current_user == "" or current_user == 0) and time() > current_user_time+60):
		printts("[{}]: user {} no old active link, requesting new one.".format(chat_id,user_id))
		bot_msg = handle_request(bot,chat_id,user_id,captcha_timeout, lang)
		
	elif current_user != user_id:
		if time() > current_user_time+(captcha_timeout*60):
			printts("[{}]: user {} old link timed out, requesting new".format(chat_id,user_id))
			bot_msg = handle_request(bot,chat_id,user_id,captcha_timeout, lang)
		else:
			mins_left = math.floor((current_user_time+(captcha_timeout*60)-time())/60)
			if mins_left <=0:
				mins_left = 1
			printts("[{}]: user {} old link still active".format(chat_id,user_id))
			bot_msg = TEXT[lang]["PROTECTION_IN_PROCESS"].format(mins_left)
	elif time() > current_user_time+(captcha_timeout*60):
		printts("[{}]: user {} old link timed out, requesting new".format(chat_id,user_id))
		bot_msg = handle_request(bot,chat_id,user_id,captcha_timeout, lang)
	else:
		bot_msg = TEXT[lang]["PROTECTION_REQUESTED"]
	bot.send_message(user_id, bot_msg,parse_mode=ParseMode.HTML)
	bot.answer_callback_query(query_id)

def set_public_group(bot,group_id,user_id,lang,query_id):
	bot_msg = TEXT[lang]["PUBLIC_NOTES_ACCESS"]
	save_config_property(int(user_id),"Current_Note_Group",int(group_id))
	trigger_list = get_chat_config(group_id,"Trigger_List")
	for key in trigger_list:
		bot_msg+="\n- <code>{}{}</code>".format(CONST["INIT_TRIGGER_CHAR"],key)
	bot.send_message(user_id, bot_msg,parse_mode=ParseMode.HTML)
	bot.answer_callback_query(query_id)

def list_admin_groups(bot,user_id):
	admin_list = []
	for group in files_config_list:
		if group["ID"] not in admin_list and tlg_user_is_admin(bot, user_id, group["ID"]):
			admin_list.append(group["ID"])
	return admin_list


def get_connected_group(bot,user_id):
	connected_group = get_chat_config(user_id,"Connected_Group")
	if connected_group < 0 and tlg_user_is_admin(bot, user_id, connected_group):
		return connected_group
	return 1

def send_not_connected(bot,chat_id):
	lang = get_chat_config(chat_id,"Language")
	bot_msg = TEXT[lang]["NOT_CONNECTED"]
	bot.send_message(chat_id,bot_msg)

def send_command_list(bot,update):
	chat_id = update.message.chat_id
	chat_type = update.message.chat.type
	lang = get_chat_config(chat_id, "Language")
	user_id = update.message.from_user.id
	if chat_type == "private":
		commands_text = TEXT[lang]["USER_COMMANDS"]
		bot.send_message(chat_id, commands_text,parse_mode=ParseMode.HTML)
		if get_chat_config(chat_id,"Connected_Group") < 0:
			commands_group = TEXT[lang]["COMMANDS"]
			bot.send_message(chat_id, commands_group,parse_mode=ParseMode.HTML)
		if is_owner(user_id):
			commands_owner = TEXT[lang]["OWNER_COMMANDS"]
			bot.send_message(chat_id, commands_owner,parse_mode=ParseMode.HTML)
	elif tlg_user_is_admin(bot, user_id, chat_id):
		commands_text = TEXT[lang]["COMMANDS"]
		tlg_msg_to_selfdestruct(update.message)
		tlg_send_selfdestruct_msg(bot, chat_id, commands_text,reply_to_message_id=update.message.message_id)
	else:
		tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["CMD_NOT_ALLOW"],reply_to_message_id=update.message.message_id)

def send_to_owner(bot,chat_id, message):
	try:
		error = traceback.format_exc()
		printts("[{}]: {}".format(chat_id,error))
		bot.send_message(SECRETS["OWNER"],TEXT["EN"]["OWNER_ERROR_MSG"].format(chat_id,str(message),error),parse_mode=ParseMode.HTML)
	except Exception:
		bot.send_message(SECRETS["OWNER"],TEXT["EN"]["OWNER_ERROR_MSG"].format(chat_id,str(message),error))
		printts("[{}]: {}".format(chat_id,traceback.format_exc()))

def is_owner(id):
	return int(id) == int(SECRETS["OWNER"])

def is_muted(chat_id,user_id):
	new_list = get_chat_config(chat_id,"Muted_List")
	for user in get_chat_config(chat_id,"Muted_List"):
		if user["id"] == user_id:
			if user["time"] > time():
				return True
			else:
				new_list.remove(user)
				return False
	save_config_property(chat_id,"Muted_List",new_list)
	return False

def delete_from_muted_list(muted_list,user_id):
	new_list = []
	for user in muted_list:
		if not user["id"] == user_id:
			new_list.append(user)
	return new_list


def is_beginner(chat_id,user_id):
	for user in get_chat_config(chat_id,"Beginner_List"):
		if user["id"] == user_id:
			return True
	return False

def delete_if_muted(bot,update):
	msg = update.message
	if not msg == None:
		chat_id = msg.chat_id
		user_id = msg.from_user.id
		msg_id = msg.message_id
		muted = is_muted(chat_id,user_id)
		beginner = is_beginner(chat_id,user_id)
		if muted or beginner:
			bot.delete_message(chat_id,msg_id)
			return True
	return False

def message_to_html(og_text,entities,cmd_offset=0):
	text = og_text
	entity_types = {
		"bold" : "b",
		"italic" : "i",
		"underline" : "u",
		"strikethrough" : "s",
		"code" : "code",
		"pre": "pre",
	}
	last_end=0
	counter = 0
	for e in entities:
		if e.type in entity_types:
			if last_end > e.offset:
				counter-=last_end_length
			tag = entity_types[e.type]
			text = text[:e.offset+counter-cmd_offset]+"<"+tag+">"+text[e.offset+counter-cmd_offset:e.offset+e.length+counter-cmd_offset]+"</"+tag+">"+text[e.offset+counter+e.length-cmd_offset:]
			counter +=5+(len(tag)*2)
			if last_end > e.offset:
				counter+=last_end_length
			last_end_length = 3+len(tag)
		elif e.type == "text_link" and len(e.url) > 0:
			if last_end > e.offset:
				counter-=last_end_length
			url = e.url
			text = text[:e.offset+counter-cmd_offset]+"<a href='{}'>".format(url)+text[e.offset+counter-cmd_offset:e.offset+e.length+counter-cmd_offset]+"</a>"+text[e.offset+counter+e.length-cmd_offset:]
			counter+=15+len(url)
			if last_end > e.offset:
				counter+=last_end_length
			last_end_length=4
		elif e.type == "text_mention":
			if last_end > e.offset:
				counter-=last_end_length
			user = e.user 
			url = "tg://user?id={}".format(user["id"])
			text = text[:e.offset+counter-cmd_offset]+"<a href='{}'>".format(url)+text[e.offset+counter-cmd_offset:e.offset+e.length+counter-cmd_offset]+"</a>"+text[e.offset+counter+e.length-cmd_offset:]
			counter+=15+len(url)
			if last_end > e.offset:
				counter+=last_end_length
			last_end_length=4
		last_end = e.offset + e.length
	return text
####################################################################################################

### JSON chat config file functions ###

def get_default_config_data():
	'''Get default config data structure'''
	config_data = OrderedDict(
	[
		("Title", CONST["INIT_TITLE"]),
		("Link", CONST["INIT_LINK"]),
		("Enabled", CONST["INIT_ENABLE"]),
		("Restrict_Non_Text", CONST["INIT_RESTRICT_NON_TEXT_MSG"]),
		("Captcha_Time", CONST["INIT_CAPTCHA_TIME_MIN"]),
		("Captcha_Difficulty_Level", CONST["INIT_CAPTCHA_DIFFICULTY_LEVEL"]),
		("Captcha_Chars_Mode", CONST["INIT_CAPTCHA_CHARS_MODE"]),
		("Language", CONST["INIT_LANG"]),
		("Welcome_Msg", CONST["INIT_WELCOME_MSG"]),
		("Last_User_Solve", 0),
		("User_Solve_Result", "0"),
		("Delete_Welcome", True),
		("Delete_Notes", True),
		("Ignore_List", []),
		("Allowed",False),
		("Protected",False),
		("Protection_Current_User",0),
		("Protection_Current_Time",0),
		("Connected_Group",0),
		("Trigger_List", {}),
		("Question_List", {}),
		("Trigger_Char", CONST["INIT_TRIGGER_CHAR"]),
		("Last_Welcome_Msg", [0,0]),
		("Invite_Hash", ""),
		("Invite_Hash_time", 0),
		("Muted_List", []),
		("Beginner_List", []),
		("Mute_Time", 3600),
		("Public_Notes", False),
		("Current_Note_Group", 0),
		("Allow_Bots", False),
		("Filters_Enabled",False),
		("Filter_List", {}),
		("Delete_Info", False)
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
			save_config_property(chat_id, param, config_data[param])
		if param not in config_data:
			config_data[param] = get_default_config_data()[param]
			save_config_property(chat_id,param,config_data[param])
	else:
		config_data = get_default_config_data()
		save_config_property(chat_id, param, config_data[param])
	return config_data[param]


def get_chat_config_file(chat_id):
	'''Determine chat config file from the list by ID. Get the file if exists or create it if not'''
	global files_config_list
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

def tlg_check_invite_hash(invite_hash):
	'''Check if the specified hash link is valid, uses telethon'''
	valid = os.popen('python3 check_invite.py {}'.format(invite_hash)).read()
	if "not" in valid:
		return False
	elif "valid" in valid:
		return True
	return False

def tlg_user_is_admin(bot, user_id, chat_id):
	'''Check if the specified user is an Administrator of a group given by IDs'''
	try:
		group_admins = bot.get_chat_administrators(chat_id)
	except Exception:
		return None
	for admin in group_admins:
		if user_id == admin.user.id:
			return True
	return False


def tlg_get_bot_admin_privileges(bot, chat_id):
	'''Get the actual Bot administration privileges'''
	try:
		bot_data = bot.get_me()
	except Exception:
		return None
	bot_admin_privileges = OrderedDict(
	[
		("can_change_info", bot_data.can_change_info),
		("can_delete_messages", bot_data.can_delete_messages),
		("can_restrict_members", bot_data.can_restrict_members),
		("can_invite_users", bot_data.can_invite_users),
		("can_pin_messages", bot_data.can_pin_messages),
		("can_promote_members", bot_data.can_promote_members)
	])
	return bot_admin_privileges


def tlg_send_selfdestruct_msg(bot, chat_id, message, markdown = True,reply_to_message_id=None,disable_web_page_preview=True):
	'''tlg_send_selfdestruct_msg_in() with default delete time'''
	return tlg_send_selfdestruct_msg_in(bot, chat_id, message, CONST["T_DEL_MSG"],markdown,reply_to_message_id=reply_to_message_id, disable_web_page_preview=disable_web_page_preview)


def tlg_msg_to_selfdestruct(message):
	'''tlg_msg_to_selfdestruct_in() with default delete time'''
	tlg_msg_to_selfdestruct_in(message, CONST["T_DEL_MSG"])


def tlg_send_selfdestruct_msg_in(bot, chat_id, message, time_delete_min, markdown= True, reply_to_message_id=None,disable_web_page_preview=True):
	'''Send a telegram message that will be auto-delete in specified time'''
	sent_msg_id = None
	# Send the message
	try:
		if markdown:
			if reply_to_message_id:
				sent_msg = bot.send_message(chat_id, message, reply_to_message_id=reply_to_message_id, parse_mode=ParseMode.HTML, disable_web_page_preview=disable_web_page_preview, disable_notification=True)
			else:
				sent_msg = bot.send_message(chat_id, message, parse_mode=ParseMode.HTML, disable_web_page_preview=disable_web_page_preview, disable_notification=True)
		else:
			if reply_to_message_id:
				sent_msg = bot.send_message(chat_id, message, reply_to_message_id=reply_to_message_id, disable_web_page_preview=disable_web_page_preview, disable_notification=True)
			else:
				sent_msg = bot.send_message(chat_id, message, disable_web_page_preview=disable_web_page_preview, disable_notification=True)

		tlg_msg_to_selfdestruct_in(sent_msg, time_delete_min)
		sent_msg_id = sent_msg["message_id"]
	# It has been an unsuccesfull sent
	except Exception as e:
		printts("[{}] {}".format(chat_id, str(e)))
	return sent_msg_id


def tlg_msg_to_selfdestruct_in(message, time_delete_min):
	'''Add a telegram message to be auto-delete in specified time'''
	global to_delete_in_time_messages_list
	# Check if provided message has all necessary attributtes
	if message is None:
		return False
	if not hasattr(message, "chat_id"):
		return False
	if not hasattr(message, "message_id"):
		return False
	if not hasattr(message, "from_user"):
		return False
	else:
		if not hasattr(message.from_user, "id"):
			return False
	# Get sent message ID and calculate delete time
	chat_id = message.chat_id
	user_id = message.from_user.id
	msg_id = message.message_id
	destroy_time = time() + (time_delete_min*60)
	# Add sent message data to to-delete messages list
	sent_msg_data = OrderedDict([("Chat_id", None), ("User_id", None),
			("Msg_id", None), ("delete_time", None)])
	sent_msg_data["Chat_id"] = chat_id
	sent_msg_data["User_id"] = user_id
	sent_msg_data["Msg_id"] = msg_id
	sent_msg_data["delete_time"] = destroy_time
	to_delete_in_time_messages_list.append(sent_msg_data)
	return True


def tlg_delete_msg(bot, chat_id, msg_id):
	'''Try to remove a telegram message'''
	return_code = 0
	if msg_id is not None:
		try:
			bot.delete_message(chat_id, msg_id)
			return_code = 1
		except Exception as e:
			printts("[{}] {}".format(chat_id, str(e)))
			# Message is already deleted
			if str(e) == "Message to delete not found":
				return_code = -1
			# The bot has no privileges to delete messages
			elif str(e) == "Message can't be deleted":
				return_code = -2
	return return_code


def tlg_ban_user(bot, chat_id, user_id):
	'''Telegram Ban a user of an specified chat'''
	return_code = 0
	try:
		user_data = bot.getChatMember(chat_id, user_id)
		if (user_data['status'] != "left") and (user_data['status'] != "kicked"):
			bot.kickChatMember(chat_id, user_id)
			return_code = 1
		else:
			return_code = -1
	except Exception as e:
		printts("[{}] {}".format(chat_id, str(e)))
		if str(e) == "Not enough rights to restrict/unrestrict chat member":
			return_code = -2
		elif str(e) == "User is an administrator of the chat":
			return_code = -3
	return return_code


def tlg_kick_user(bot, chat_id, user_id):
	'''Telegram Kick (no ban) a user of an specified chat'''
	return_code = 0
	try:
		user_data = bot.getChatMember(chat_id, user_id)
		if (user_data['status'] != "left") and (user_data['status'] != "kicked"):
			bot.kickChatMember(chat_id, user_id)
			bot.unbanChatMember(chat_id, user_id)
			return_code = 1
		else:
			return_code = -1
	except Exception as e:
		printts("[{}] {}".format(chat_id, str(e)))
		if str(e) == "Not enough rights to restrict/unrestrict chat member":
			return_code = -2
		elif str(e) == "User is an administrator of the chat":
			return_code = -3
	return return_code


def tlg_check_chat_type(bot, chat_id_or_alias):
	'''Telegram check if a chat exists and what type it is (user, group, channel).'''
	chat_type = None
	# Check if it is a group or channel
	try:
		get_chat = bot.getChat(chat_id_or_alias)
		chat_type = getattr(get_chat, "type", None)
	except Exception as e:
		if str(e) != "Chat not found":
			printts("[{}] {}".format(chat_id_or_alias, str(e)))
	return chat_type


def tlg_leave_chat(bot, chat_id):
	'''Telegram Bot try to leave a chat.'''
	left = False
	try:
		if bot.leave_chat(chat_id):
			left = True
	except Exception as e:
		printts("[{}] {}".format(chat_id, str(e)))
	return left


def tlg_restrict_user(bot, chat_id, user_id, send_msg=None, send_media=None, 
		send_stickers_gifs=None, insert_links=None, send_polls=None, 
		invite_members=None, pin_messages=None, change_group_info=None):
	'''Telegram Bot try to restrict user permissions in a group.'''
	result = False
	try:
		permissions = ChatPermissions(send_msg, send_media, send_polls, send_stickers_gifs, 
			insert_links, change_group_info, invite_members, pin_messages)
		result = bot.restrictChatMember(chat_id, user_id, permissions)
	except Exception as e:
		printts("[{}] {}".format(chat_id, str(e)))
		result = False
	return result

####################################################################################################

### Received Telegram not-command messages handlers ###

def msg_new_user(update: Update, context: CallbackContext):
	'''New member join the group event handler'''
	try:
		global to_delete_join_messages_list
		global new_users_list
		bot = context.bot
		# Get message data
		chat_id = update.message.chat_id
		# Determine configured bot language in actual chat
		lang = get_chat_config(chat_id, "Language")
		# Leave the chat if it is a channel
		msg = getattr(update, "message", None)
		if get_chat_config(chat_id,"Delete_Info"):
			tlg_msg_to_selfdestruct(update.message)
		if msg.chat.type == "channel":
			send_to_owner(bot,chat_id,TEXT["EN"]["BOT_LEFT_GROUP"].format(chat_id))
			tlg_send_selfdestruct_msg_in(bot, chat_id, TEXT["EN"]["BOT_LEAVE_CHANNEL"]+" {}".format(chat_id))
			tlg_leave_chat(bot, chat_id)
			return
		if msg.chat.type != "private" and not get_chat_config(msg.chat_id,"Allowed"):
				send_to_owner(bot,chat_id,TEXT["EN"]["BOT_LEFT_GROUP"].format(chat_id))
				tlg_send_selfdestruct_msg(bot, msg.chat_id,TEXT["EN"]["GROUP_NOT_ALLOWED"].format(SECRETS["OWNER_NAME"],chat_id,CONST["REPOSITORY"]))
				tlg_leave_chat(bot, msg.chat_id)
				return
		# For each new user that join or has been added
		for join_user in update.message.new_chat_members:
			join_user_id = join_user.id
			# Get user name
			if join_user.name is not None:
				join_user_name = join_user.name
			else:
				join_user_name = join_user.full_name
			# Add an unicode Left to Right Mark (LRM) to user name (names fix for arabic, hebrew, etc.)
			join_user_name = add_lrm(join_user_name)
			# If the user name is too long, truncate it to 35 characters
			if len(join_user_name) > 35:
				join_user_name = join_user_name[0:35]
			# If the added user is myself (this Bot)
			if bot.id == join_user_id:
				# Get the language of the Telegram client software the Admin that has added the Bot 
				# has, to assume this is the chat language and configure Bot language of this chat
				admin_language = update.message.from_user.language_code[0:2].upper()
				if admin_language not in TEXT:
					admin_language = CONST["INIT_LANG"]
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
				try:
					bot.send_message(chat_id, TEXT[admin_language]["START"])
				except Exception as e:
					printts("[{}] {}".format(chat_id, str(e)))
					pass
			# The added user is not myself (not this Bot)
			else:
				printts(" ")
				printts("[{}] New join detected: {} ({})".format(chat_id, join_user_name, join_user_id))
				# Get and update chat data
				chat_title = update.message.chat.title
				if chat_title:
					save_config_property(chat_id, "Title", chat_title)
				# Add an unicode Left to Right Mark (LRM) to chat title (fix for arabic, hebrew, etc.)
				chat_title = add_lrm(chat_title)
				chat_link = update.message.chat.username
				if chat_link:
					chat_link = "@{}".format(chat_link)
					save_config_property(chat_id, "Link", chat_link)
				# Ignore Admins
				if tlg_user_is_admin(bot, join_user_id, chat_id):
					printts("[{}] User is an administrator. Skipping the captcha process.".format(chat_id))
					continue
				# Ignore if the member that has been join the group is a Bot
				if join_user.is_bot:
					printts("[{}] User is a Bot. Skipping the captcha process.".format(chat_id))
					continue
				# Ignore if the member that has joined is in ignore list
				ignored_ids = get_chat_config(chat_id, "Ignore_List")
				if join_user_id in ignored_ids:
					printts("[{}] User is in ignore list. Skipping the captcha process.".format(chat_id))
					continue
				current_user = get_chat_config(chat_id,"Protection_Current_User")
				current_user_time = get_chat_config(chat_id,"Protection_Current_Time")
				protected = get_chat_config(chat_id,"Protected")
				captcha_timeout = get_chat_config(chat_id,"Captcha_Time")
				if protected and current_user == join_user_id and time() <= current_user_time+(captcha_timeout*60):
					send_welcome_msg(bot,chat_id,update,chat_id)
					save_config_property(chat_id,"Protection_Current_User",0)
					printts("[{}] User joined after protected authorization!".format(chat_id))
					continue
				elif protected:
					kick_user(bot,chat_id,join_user_id,update.message.from_user.username)
					printts("[{}] User kicked because of protection!".format(chat_id))
					revoke_group_link(bot,chat_id)
					continue
				# Check and remove previous join messages of that user (if any)
				i = 0
				while i < len(to_delete_join_messages_list):
					msg = to_delete_join_messages_list[i]
					if (msg["user_id"] == join_user_id) and (msg["chat_id"] == chat_id):
						tlg_delete_msg(bot, msg["chat_id"], msg["msg_id_join0"].message_id)
						tlg_delete_msg(bot, msg["chat_id"], msg["msg_id_join1"])
						tlg_delete_msg(bot, msg["chat_id"], msg["msg_id_join2"])
						if msg in to_delete_join_messages_list:
							to_delete_join_messages_list.remove(msg)
					i = i + 1
				# Ignore if the captcha protection is not enable in this chat
				captcha_enable = get_chat_config(chat_id, "Enabled")
				if not captcha_enable:
					printts("[{}] Captcha is not enabled in this chat".format(chat_id))
					continue
				# Determine configured bot language in actual chat
				captcha_level = get_chat_config(chat_id, "Captcha_Difficulty_Level")
				captcha_chars_mode = get_chat_config(chat_id, "Captcha_Chars_Mode")
				# Generate a pseudorandom captcha send it to telegram group and program message 
				# selfdestruct
				captcha = create_image_captcha(str(join_user_id), captcha_level, captcha_chars_mode)
				captcha_timeout = get_chat_config(chat_id, "Captcha_Time")
				img_caption = TEXT[lang]["NEW_USER_CAPTCHA_CAPTION"].format(join_user_name,
						chat_title, str(captcha_timeout))
				# Prepare inline keyboard button to let user request another catcha
				keyboard = [[InlineKeyboardButton(TEXT[lang]["OTHER_CAPTCHA_BTN_TEXT"],
						callback_data=join_user_id)]]
				reply_markup = InlineKeyboardMarkup(keyboard)
				send_problem = False
				printts("[{}] Sending captcha message: {}...".format(chat_id, captcha["number"]))
				try:
					# Note: Img caption must be <= 1024 chars
					sent_img_msg = bot.send_photo(chat_id=chat_id, photo=open(captcha["image"],"rb"),
							reply_markup=reply_markup, caption=img_caption, timeout=20)
				except Exception as e:
					printts("[{}] {}".format(chat_id, str(e)))
					if str(e) != "Timed out":
						send_problem = True
					else:
						printts("sent_img_msg: {}".format(sent_img_msg))
				# Remove sent captcha image file from file system
				if path.exists(captcha["image"]):
					remove(captcha["image"])
				if not send_problem:
					# Add sent image to self-destruct list
					if not tlg_msg_to_selfdestruct_in(sent_img_msg, captcha_timeout+0.5):
						printts("[{}] sent_img_msg does not have all expected attributes. "
								"Scheduled for deletion".format(chat_id))
					# Default user data
					new_user = \
					{
						"chat_id": chat_id,
						"user_id": join_user_id,
						"user_name": join_user_name,
						"captcha_num": captcha["number"],
						"join_time": time(),
						"join_retries": 1,
						"kicked_ban": False
					}

					# Add user to mute list until he solves the captcha
					muted_list = get_chat_config(chat_id,"Muted_List")
					muted_list.append({"id": join_user_id, "time": time()+FOREVER})
					save_config_property(chat_id,"Muted_List",muted_list)
					# Check if this user was before in the chat without solve the captcha
					prev_user_data = None
					for user in new_users_list:
						if user["chat_id"] == new_user["chat_id"]:
							if user["user_id"] == new_user["user_id"]:
								prev_user_data = user
					if prev_user_data is not None:
						# Keep join retries and remove previous user data from list
						new_user["join_retries"] = prev_user_data["join_retries"]
						prev_pos = new_users_list.index(prev_user_data)
						new_users_list[prev_pos] = new_user
					else:
						# Add new user data to lists
						new_users_list.append(new_user)
					# Add join messages to delete
					msg = \
					{
						"chat_id": chat_id,
						"user_id": join_user_id,
						"msg_id_join0": update.message,
						"msg_id_join1": sent_img_msg.message_id,
						"msg_id_join2": None
					}
					to_delete_join_messages_list.append(msg)
					printts("[{}] Captcha send process complete.".format(chat_id))
					printts(" ")
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def handle_service_message(update: Update, context: CallbackContext):
	bot = context.bot
	message = update.message
	chat_id = message.chat_id
	if get_chat_config(chat_id,"Delete_Info"):
		tlg_msg_to_selfdestruct(update.message)
				
def msg_notext(update: Update, context: CallbackContext):
	'''All non-text messages handler.'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
						return
		# Check for normal or edited message
		msg = getattr(update, "message", None)
		if msg is None:
			msg = getattr(update, "edited_message", None)
		# Ignore if message comes from a private chat
		if msg.chat.type == "private":
			return
		# Ignore if message comes from a channel
		if msg.chat.type == "channel":
			return
		# Ignore if captcha protection is not enable int his chat
		captcha_enable = get_chat_config(msg.chat_id, "Enabled")
		if not captcha_enable:
			return
		# Get message data
		chat_id = msg.chat_id
		user_id = msg.from_user.id
		msg_id = msg.message_id
		# Determine configured bot language in actual chat
		lang = get_chat_config(chat_id, "Language")
		# Search if this user is a new user that has not completed the captcha yet
		i = 0
		while i < len(new_users_list):
			new_user = new_users_list[i]
			# If not the user of this message, continue to next iteration
			if new_user["user_id"] != user_id:
				i = i + 1
				continue
			# If not the chat for expected user captcha number
			if new_user["chat_id"] != chat_id:
				i = i + 1
				continue
			# Remove send message and notify that not text messages are not allowed until solve captcha
			printts("[{}] Removing non-text message sent by {}".format(chat_id, new_user["user_name"]))
			tlg_delete_msg(bot, chat_id, msg_id)
			bot_msg = TEXT[lang]["NOT_TEXT_MSG_ALLOWED"].format(new_user["user_name"])
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)
			break
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def msg_nocmd(update: Update, context: CallbackContext):
	'''Non-command text messages handler'''
	try:
		global to_delete_join_messages_list
		global new_users_list
		bot = context.bot
		# Check for normal or edited message
		msg = getattr(update, "message", None)
		if msg is None:
			msg = getattr(update, "edited_message", None)
		# Ignore if message comes from a channel
		if msg.chat.type == "channel":
			return
		msg_text = getattr(msg, "text", None)
		chat_id = msg.chat_id
		user_id = msg.from_user.id
		msg_id = msg.message_id
		if len(msg_text) != 4 and delete_if_muted(bot,update):
			return
		#add output if beginner
		if msg.chat.type != "private" and not get_chat_config(chat_id,"Allowed"):
			send_to_owner(bot,chat_id,TEXT["EN"]["BOT_LEFT_GROUP"].format(chat_id))
			tlg_send_selfdestruct_msg(bot, msg.chat_id,TEXT["EN"]["GROUP_NOT_ALLOWED"].format(SECRETS["OWNER_NAME"],chat_id,CONST["REPOSITORY"]))
			tlg_leave_chat(bot, msg.chat_id)
			return
		# If message doesnt has text, check for caption fields (for no text msgs and resended ones)

		if msg_text is None:
			msg_text = getattr(msg, "caption_html", None)
		if msg_text is None:
			msg_text = getattr(msg, "caption", None)
		# Check if message starts with # -> print the trigger
		if msg.chat.type == "private":
			connected = get_connected_group(bot,user_id)
			public_group_id = get_chat_config(chat_id,"Current_Note_Group")
			if connected < 0:
				chat_id = connected
				public_group_id = connected
			if public_group_id < 0 and msg_text[0] == CONST["INIT_TRIGGER_CHAR"]:
				if get_chat_config(public_group_id,"Public_Notes") or connected == public_group_id:
					trigger_list = get_chat_config(public_group_id,"Trigger_List")
					trigger_msg = trigger_list.pop(msg_text[1:],"")
					if len(trigger_msg) > 0:
						bot.send_message(msg.chat_id, trigger_msg,parse_mode=ParseMode.HTML, disable_web_page_preview=True)
					elif not get_chat_config(msg.chat_id,"Allow_Bots"):
						bot.delete_message(msg.chat_id,msg.message_id)
				else:
					lang = get_chat_config(msg.chat_id, "Language")
					bot.send_message(msg.chat_id, TEXT[lang]["PUBLIC_NOTES_INACCESSIBLE"],parse_mode=ParseMode.HTML)
				return
			elif msg_text[0] == CONST["INIT_TRIGGER_CHAR"]:
				bot.send_message(msg.chat_id, TEXT[lang]["PUBLIC_NOTES_NO_CONNECTION"],parse_mode=ParseMode.HTML)
				return
		if msg_text[0] == get_chat_config(chat_id, "Trigger_Char"):
			trigger_list = get_chat_config(chat_id,"Trigger_List")
			trigger_msg = trigger_list.pop(msg_text[1:],"")
			tlg_msg_to_selfdestruct(update.message)
			reply_to_id = update.message.message_id
			reply_to_msg = getattr(update.message,"reply_to_message", None)
			if reply_to_msg:
				reply_to_id = reply_to_msg.message_id
			if len(trigger_msg) > 0 and get_chat_config(chat_id,"Delete_Notes"):
				tlg_send_selfdestruct_msg(bot, chat_id, trigger_msg,reply_to_message_id=reply_to_id, disable_web_page_preview=True)
				return
			elif len(trigger_msg) > 0:
				bot.send_message(msg.chat_id, trigger_msg,parse_mode=ParseMode.HTML,reply_to_message_id=reply_to_id, disable_web_page_preview=True)
				return
			elif not get_chat_config(msg.chat_id,"Allow_Bots"):
				bot.delete_message(msg.chat_id,msg.message_id)
				return
		# Handle user captcha if message is private
		if msg.chat.type == "private":
			msg_text = getattr(msg, "text", None)
			lang = get_chat_config(msg.chat_id, "Language")
			if len(msg_text) == 4:
				if msg_text == str(get_chat_config(msg.chat_id,"User_Solve_Result")):
					bot_msg =TEXT[lang]["USER_START"]
					save_config_property(msg.chat_id,"Last_User_Solve",time())
					protected_list = get_protected_list()
					reply_markup = InlineKeyboardMarkup(protected_list)
					bot.send_message(msg.chat_id, TEXT[lang]["USER_START"],reply_markup=reply_markup)
				else:
					bot_msg = TEXT[lang]["USER_CAPTCHA_FAILED"]
					tlg_send_selfdestruct_msg_in(bot, msg.chat_id, bot_msg, 5)
			else:
				bot_msg = TEXT[lang]["HELP"]
				tlg_send_selfdestruct_msg_in(bot, msg.chat_id, bot_msg, 5)
			return
		if msg_text[0]=='/' and not get_chat_config(msg.chat_id,"Allow_Bots"):
			bot.delete_message(msg.chat_id,msg.message_id)
		# Ignore if captcha protection is not enable in this chat
		captcha_enable = get_chat_config(msg.chat_id, "Enabled")
		if not captcha_enable:
			return
		# Check if message has a text link (embedded url in text) and get it
		msg_entities = getattr(msg, "entities", None)
		if msg_entities is not None:
			for entity in msg_entities:
				url = getattr(entity, "url", None)
				if url is not None:
					if url != "":
						if msg_text is None:
							msg_text = url
						else:
							msg_text = "{} [{}]".format(msg_text, url)
						break
		# Get others message data

		# Get and update chat data
		chat_title = msg.chat.title
		if chat_title:
			save_config_property(chat_id, "Title", chat_title)
		chat_link = msg.chat.username
		if chat_link:
			chat_link = "@{}".format(chat_link)
			save_config_property(chat_id, "Link", chat_link)
		user_name = msg.from_user.full_name
		if msg.from_user.username is not None:
			user_name = "{}(@{})".format(user_name, msg.from_user.username)
		# Set default text message if not received
		if msg_text is None:
			msg_text = "[Not a text message]"
		# Determine configured bot language in actual chat
		lang = get_chat_config(chat_id, "Language")
		# Search if this user is a new user that has not completed the captcha yet
		i = 0
		while i < len(new_users_list):
			new_user = new_users_list[i]
			# If not the user of this message, continue to next iteration
			if new_user["user_id"] != user_id:
				i = i + 1
				continue
			# If not the chat for expected user captcha number
			if new_user["chat_id"] != chat_id:
				i = i + 1
				continue
			# Check if the expected captcha solve number is in the message
			printts("[{}] Received captcha reply from {}: {}".format(chat_id,
					new_user["user_name"], msg_text))
			if new_user["captcha_num"].lower() in msg_text.lower():
				# Remove join messages
				printts("[{}] Captcha solved by {}".format(chat_id, new_user["user_name"]))
				j = 0
				while j < len(to_delete_join_messages_list):
					msg_del = to_delete_join_messages_list[j]
					if (msg_del["user_id"] == user_id) and (msg_del["chat_id"] == chat_id):
						# Uncomment next line to remove "user join" message too
						#tlg_delete_msg(bot, msg_del["chat_id"], msg_del["msg_id_join0"].message_id)
						tlg_delete_msg(bot, msg_del["chat_id"], msg_del["msg_id_join1"])
						tlg_delete_msg(bot, msg_del["chat_id"], msg_del["msg_id_join2"])
						if msg_del in to_delete_join_messages_list:
							to_delete_join_messages_list.remove(msg_del)
						break
					j = j + 1
				# Remove user captcha numbers message
				#add deleting all user captcha messages that were wrong
				tlg_delete_msg(bot, chat_id, msg.message_id)
				bot_msg = TEXT[lang]["CAPTCHA_SOLVED"].format(new_user["user_name"])
				# Set Bot to auto-remove captcha solved message too after 5mins
				#            tlg_send_selfdestruct_msg_in(bot, chat_id, bot_msg, 5)
				if new_user in new_users_list:
					new_users_list.remove(new_user)
				#remove user from muted list
				muted_list = get_chat_config(chat_id,"Muted_List")
				muted_list = delete_from_muted_list(muted_list,new_user["user_id"])
				save_config_property(chat_id,"Muted_List",muted_list)

				send_welcome_msg(bot,chat_id,update,chat_id)
				restrict_non_text_msgs = get_chat_config(chat_id, "Restrict_Non_Text")
				if restrict_non_text_msgs:
					tlg_restrict_user(bot, chat_id, user_id, send_msg=True, send_media=False, 
						send_stickers_gifs=False, insert_links=False, send_polls=False, 
						invite_members=False, pin_messages=False, change_group_info=False)
			# The provided message doesn't has the valid captcha number
			else:
				# Check if the message has 4 chars
				if len(msg_text) == 4:
					# Remove previously error message (if any)
					for msg_del in to_delete_join_messages_list:
						if (msg_del["user_id"] == user_id) and (msg_del["chat_id"] == chat_id):
							tlg_delete_msg(bot, msg_del["chat_id"], msg_del["msg_id_join2"])
					sent_msg_id = tlg_send_selfdestruct_msg(bot, chat_id,
							TEXT[lang]["CAPTCHA_INCORRECT_0"],reply_to_message_id=update.message.message_id)
					update_to_delete_join_msg_id(chat_id, user_id, "msg_id_join2", sent_msg_id)
					# Promise remove bad message data in one minute
					tlg_msg_to_selfdestruct_in(msg, 1)
				else:
					# Check if the message was just a 4 numbers msg
					if is_int(msg_text):
						# Remove previously error message (if any)
						for msg_del in to_delete_join_messages_list:
							if (msg_del["user_id"] == user_id) and (msg_del["chat_id"] == chat_id):
								tlg_delete_msg(bot, msg_del["chat_id"], msg_del["msg_id_join2"])
						sent_msg_id = tlg_send_selfdestruct_msg(bot, chat_id,
								TEXT[lang]["CAPTCHA_INCORRECT_1"],reply_to_message_id=update.message.message_id)
						update_to_delete_join_msg_id(chat_id, user_id, "msg_id_join2", sent_msg_id)
						# Promise remove bad message data in one minute
						tlg_msg_to_selfdestruct_in(msg, 1)
					else:
						# Check if the message contains any URL
						has_url = re.findall(CONST["REGEX_URLS"], msg_text)
						# Check if the message contains any alias and if it is a group or channel alias
						has_alias = False
						#alias = ""
						for word in msg_text.split():
							if (len(word) > 1) and (word[0] == '@'):
								has_alias = True
								#alias = word
								break
						# Check if the detected alias is from a valid chat (commented due to getChat 
						# request doesnt tell us if an alias is from an user, just group or channel)
						#has_alias = False
						#if has_alias:
						#    chat_type = tlg_check_chat_type(bot, alias)
						#    # A None value in chat_type is for not telegram chat found
						#    if chat_type is not None:
						#        has_alias = True
						#    else:
						#        has_alias = False
						# Remove and notify if url/alias detection
						if has_url or has_alias:
							printts("[{}] Spammer detected: {}.".format(chat_id, new_user["user_name"]))
							printts("[{}] Removing spam message: {}.".format(chat_id, msg_text))
							# Try to remove the message and notify detection
							rm_result = tlg_delete_msg(bot, chat_id, msg_id)
							if rm_result == 1:
								bot_msg = TEXT[lang]["SPAM_DETECTED_RM"].format(new_user["user_name"])
							# Check if message cant be removed due to not delete msg privileges
							if rm_result == -2:
								bot_msg = TEXT[lang]["SPAM_DETECTED_NOT_RM"].format(new_user["user_name"])
							# Get chat kick timeout and send spam detection message with autoremove
							captcha_timeout = get_chat_config(chat_id, "Captcha_Time")
							tlg_send_selfdestruct_msg_in(bot, chat_id, bot_msg, captcha_timeout)
			printts("[{}] Captcha reply process complete.".format(chat_id))
			printts(" ")
			break
		if msg.chat.type != "private" and len(msg_text) > 1:
			if get_chat_config(chat_id,"Filters_Enabled"):
				filter_list = get_chat_config(chat_id,"Filter_List")
				reply_to_id = update.message.message_id
				auto_delete = get_chat_config(chat_id,"Delete_Notes")
				for filter_string in filter_list:
					if filter_string.lower() in msg_text.lower():
						filter_text = filter_list[filter_string]
						if filter_text == "/kick":
							bot.kickChatMember(chat_id, user_id)
							bot.unbanChatMember(chat_id, user_id)
						elif filter_text == "/ban":
							tlg_ban_user(bot, chat_id, user_id)
						if auto_delete:
							tlg_send_selfdestruct_msg(bot, chat_id, filter_text,reply_to_message_id=reply_to_id, disable_web_page_preview=True)
						else:
							bot.send_message(chat_id, filter_text,parse_mode=ParseMode.HTML,reply_to_message_id=reply_to_id, disable_web_page_preview=True)
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def button_request_captcha(update: Update, context: CallbackContext):
	'''Button "Other Captcha" pressed handler'''
	try:
		global new_users_list
		bot = context.bot
		query = update.callback_query
		# Ignore if the query come from an unexpected user
		if query.data != str(query.from_user.id) and query.data[0] != "p" and query.data[0] != "n":
			bot.answer_callback_query(query.id)
			return
		# Get query data
		chat_id = query.message.chat_id
		usr_id = query.from_user.id
		message_id = query.message.message_id
		chat_title = query.message.chat.title
		# Get chat language
		lang = get_chat_config(chat_id, "Language")

		if query.message.chat.type == "private":
			if query.data[0] == "p":
				request_group_link(bot,query.data[1:],query.from_user.id,lang,query.id)
			elif query.data[0] == "n":
				set_public_group(bot,query.data[1:],query.from_user.id,lang,query.id)
			else:
				printts("[{}] User {} requested a new captcha.".format(chat_id, usr_id))
				# Prepare inline keyboard button to let user request another catcha
				keyboard = [[InlineKeyboardButton(TEXT[lang]["OTHER_CAPTCHA_BTN_TEXT"],
						callback_data=str(query.from_user.id))]]
				reply_markup = InlineKeyboardMarkup(keyboard)
				# Get captcha timeout and set image caption
				captcha_timeout = CONST["INIT_CAPTCHA_TIME_MIN"]
				img_caption = TEXT[lang]["USER_CAPTCHA"].format(query.message.chat.username,
								str(captcha_timeout))
				# Determine configured bot language in actual chat
				captcha_level = CONST["INIT_CAPTCHA_DIFFICULTY_LEVEL"]
				captcha_chars_mode = CONST["INIT_CAPTCHA_CHARS_MODE"]
				# Generate a new captcha and edit previous captcha image message with this one
				captcha = create_image_captcha(str(usr_id), captcha_level, captcha_chars_mode)
				printts("[{}] Sending new captcha message: {}...".format(chat_id, captcha["number"]))
				bot.edit_message_media(chat_id, message_id, media=InputMediaPhoto(
						media=open(captcha["image"], "rb"), caption=img_caption),
						reply_markup=reply_markup, timeout=20)
				# Remove sent captcha image file from file system
				if path.exists(captcha["image"]):
					remove(captcha["image"])
				save_config_property(chat_id,"User_Solve_Result",captcha["number"])
				bot.answer_callback_query(query.id)
		# Add an unicode Left to Right Mark (LRM) to chat title (fix for arabic, hebrew, etc.)
		chat_title = add_lrm(chat_title)
		# Search if this user is a new user that has not completed the captcha
		i = 0
		while i < len(new_users_list):
			new_user = new_users_list[i]
			if (new_user["user_id"] == usr_id) and (new_user["chat_id"] == chat_id):
				printts("[{}] User {} requested a new captcha.".format(chat_id, new_user["user_name"]))
				# Prepare inline keyboard button to let user request another catcha
				keyboard = [[InlineKeyboardButton(TEXT[lang]["OTHER_CAPTCHA_BTN_TEXT"],
						callback_data=str(query.from_user.id))]]
				reply_markup = InlineKeyboardMarkup(keyboard)
				# Get captcha timeout and set image caption
				captcha_timeout = get_chat_config(chat_id, "Captcha_Time")
				img_caption = TEXT[lang]["NEW_USER_CAPTCHA_CAPTION"].format(new_user["user_name"],
						chat_title, str(captcha_timeout))
				# Determine configured bot language in actual chat
				captcha_level = get_chat_config(chat_id, "Captcha_Difficulty_Level")
				captcha_chars_mode = get_chat_config(chat_id, "Captcha_Chars_Mode")
				# Generate a new captcha and edit previous captcha image message with this one
				captcha = create_image_captcha(str(usr_id), captcha_level, captcha_chars_mode)
				printts("[{}] Sending new captcha message: {}...".format(chat_id, captcha["number"]))
				bot.edit_message_media(chat_id, message_id, media=InputMediaPhoto(
						media=open(captcha["image"], "rb"), caption=img_caption),
						reply_markup=reply_markup, timeout=20)
				# Set and modified to new expected captcha number
				new_user["captcha_num"] = captcha["number"]
				new_users_list[i] = new_user
				# Remove sent captcha image file from file system
				if path.exists(captcha["image"]):
					remove(captcha["image"])
				break
			i = i + 1
		printts("[{}] New captcha request process complete.".format(chat_id))
		printts(" ")
		bot.answer_callback_query(query.id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)



####################################################################################################

### Received Telegram command messages handlers ###
def error_callback(update, context):
	try:
		raise context.error
	except Exception as e:
		chat_id = update.message.chat_id
		send_to_owner(bot,chat_id,e)


def cmd_kick(update: Update, context: CallbackContext):
	bot= context.bot
	chat_id = update.message.chat_id
	user_id = update.message.from_user.id
	tlg_kick_user(bot,chat_id,user_id)
	#maybe only insult user?

def cmd_start(update: Update, context: CallbackContext):
	'''Command /start message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		msg = getattr(update, "message", None)
		chat_id = update.message.chat_id
		chat_type = update.message.chat.type
		lang = get_chat_config(chat_id, "Language")
		if chat_type == "private":
			last_solved = get_chat_config(chat_id,"Last_User_Solve")
			if time() > last_solved + (CONST["VALID_CAPTCHA_TIME"]* 60):
				show_user_captcha(bot, chat_id,msg.chat.username,lang)
			else:
				protected_list = get_protected_list()
				reply_markup = InlineKeyboardMarkup(protected_list)
				bot.send_message(chat_id, TEXT[lang]["USER_START"],reply_markup=reply_markup)
		else:
			tlg_msg_to_selfdestruct(update.message)
			if get_chat_config(chat_id,"Allowed"):
				tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["START"])
			else:
				send_to_owner(bot,chat_id,TEXT["EN"]["BOT_LEFT_GROUP"].format(chat_id))
				tlg_send_selfdestruct_msg(bot, msg.chat_id,TEXT["EN"]["GROUP_NOT_ALLOWED"].format(SECRETS["OWNER_NAME"],chat_id,CONST["REPOSITORY"]))
				tlg_leave_chat(bot, msg.chat_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def cmd_connect(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		chat_id = update.message.chat_id
		chat_type = update.message.chat.type
		user_id = update.message.from_user.id
		lang = get_chat_config(chat_id, "Language")
		chat_type = update.message.chat.type
		args = context.args
		if chat_type != "private":
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["CMD_NOT_ALLOW"],reply_to_message_id=update.message.message_id)
			return
		bot.sendChatAction(chat_id, action=ChatAction.TYPING)
		if len(args) == 1:
			if tlg_user_is_admin(bot, user_id, args[0]):
				bot_msg = TEXT[lang]["CONNECTED"].format(args[0])
				save_config_property(user_id,"Connected_Group",int(args[0]))
			else:
				bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			connected_group = get_chat_config(user_id,"Connected_Group")
			if connected_group < 0:
				connected_title = get_chat_config(connected_group,"Title")
				bot_msg = TEXT[lang]["CONNECT_NO_ARGS_BUT_CONNECTED"].format(connected_title,connected_group)
			else:
				bot_msg = TEXT[lang]["CONNECT_NO_ARGS"]
			admin_groups =  list_admin_groups(bot,user_id)
			for group in admin_groups:
				title = get_chat_config(group,"Title")
				bot_msg+="\n\n<b>{}</b>:\n<code>{}</code>".format(title,group)
		bot.send_message(chat_id, bot_msg,parse_mode=ParseMode.HTML)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_disconnect(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		chat_type = update.message.chat.type
		user_id = update.message.from_user.id
		chat_id = update.message.chat_id
		lang = get_chat_config(user_id, "Language")
		connected_group = get_chat_config(user_id,"Connected_Group")
		if chat_type != "private":
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["CMD_NOT_ALLOW"],reply_to_message_id=update.message.message_id)
			return
		if connected_group < 0:
			bot_msg = TEXT[lang]["DISCONNECTED"]
			save_config_property(user_id,"Connected_Group",0)
		else:
			bot_msg = TEXT[lang]["NOT_CONNECTED"]
		bot.send_message(user_id, bot_msg)
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def cmd_commands(update: Update, context: CallbackContext):
	'''Command /commands message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		send_command_list(bot,update)
		if update.message.chat.type != "private":
			tlg_msg_to_selfdestruct(update.message)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_time(update: Update, context: CallbackContext):
	'''Command /time message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_chat = chat_id
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
		if allow_command:
			if len(args) >= 1:
				if is_int(args[0]):
					new_time = int(args[0])
					if new_time < 1:
						new_time = 1
					if new_time <= 120:
						save_config_property(chat_id, "Captcha_Time", new_time)
						bot_msg = TEXT[lang]["TIME_CHANGE"].format(new_time)
					else:
						bot_msg = TEXT[lang]["TIME_MAX_NOT_ALLOW"]
				else:
					bot_msg = TEXT[lang]["TIME_NOT_NUM"]
			else:
				bot_msg = TEXT[lang]["TIME_NOT_ARG"]
		elif not is_admin:
			bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(print_chat, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, print_chat, bot_msg,reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def cmd_difficulty(update: Update, context: CallbackContext):
	'''Command /difficulty message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_chat = chat_id
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
		if allow_command:
			if len(args) >= 1:
				if is_int(args[0]):
					new_difficulty = int(args[0])
					if new_difficulty < 1:
						new_difficulty = 1
					if new_difficulty > 5:
						new_difficulty = 5
					save_config_property(chat_id, "Captcha_Difficulty_Level", new_difficulty)
					bot_msg = TEXT[lang]["DIFFICULTY_CHANGE"].format(new_difficulty)
				else:
					bot_msg = TEXT[lang]["DIFFICULTY_NOT_NUM"]
			else:
				bot_msg = TEXT[lang]["DIFFICULTY_NOT_ARG"]
		elif not is_admin:
			bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(print_chat, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg,reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def cmd_captcha_mode(update: Update, context: CallbackContext):
	'''Command /captcha_mode message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_chat = chat_id
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
		if allow_command:
			if len(args) >= 1:
				new_captcha_mode = args[0]
				if (new_captcha_mode == "nums") or (new_captcha_mode == "hex") \
						or (new_captcha_mode == "ascii"):
					save_config_property(chat_id, "Captcha_Chars_Mode", new_captcha_mode)
					bot_msg = TEXT[lang]["CAPTCHA_MODE_CHANGE"].format(new_captcha_mode)
				else:
					bot_msg = TEXT[lang]["CAPTCHA_MODE_INVALID"]
			else:
				bot_msg = TEXT[lang]["CAPTCHA_MODE_NOT_ARG"]
		elif not is_admin:
			bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(print_chat, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg,reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def cmd_welcome_message(update: Update, context: CallbackContext):
	'''Command /welcome_msg message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_chat = chat_id
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
		if allow_command:
			send_welcome_msg(bot,chat_id, update, print_chat)
			return
		elif not is_admin:
			bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(print_chat, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)
	except Exception as e:
		send_to_owner(bot,chat_id,e)



def cmd_set_welcome_message(update: Update, context: CallbackContext):
	'''Command /set_welcome_msg message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_chat = chat_id
		valid = None
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
			tlg_msg_to_selfdestruct(update.message)
		if allow_command:
			if len(args) >= 1:
				old_welcome_msg = get_chat_config(chat_id,"Welcome_Msg")
				#welcome_msg = " ".join(args)
				welcome_msg=" ".join(update.message.text.split(" ")[1:])
				offset = len(update.message.text)-len(welcome_msg)
				welcome_msg = message_to_html(welcome_msg,update.message.entities,offset)
				welcome_msg = welcome_msg.replace("$user", "{0}").replace("$name","{1}").replace("$id","{2}").replace("$link","{3}").replace("$group","{4}")
				welcome_msg = welcome_msg[:CONST["MAX_WELCOME_MSG_LENGTH"]]
				if welcome_msg == "disable":
					welcome_msg = '-'
					bot_msg = TEXT[lang]["WELCOME_MSG_UNSET"]
				else:
					bot_msg = TEXT[lang]["WELCOME_MSG_SET"]
				save_config_property(chat_id, "Welcome_Msg", welcome_msg)
				valid = send_welcome_msg(bot,chat_id, update, print_chat)
				if valid == None:
					save_config_property(chat_id, "Welcome_Msg", old_welcome_msg)
			else:
				bot_msg = TEXT[lang]["WELCOME_MSG_SET_NOT_ARG"]
		elif not is_admin:
			bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if valid == None:
			bot_msg = TEXT[lang]["WELCOME_MSG_FAILED"]
		if chat_type == "private":
			bot.send_message(print_chat, bot_msg)
		else:		
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg,reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_add_trigger(update: Update, context: CallbackContext):
	'''Command /add_trigger message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		print_id = chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
		if allow_command:
			reply_to = getattr(update.message,"reply_to_message", None)
			if reply_to != None and len(args) >= 1:
				name = args[0]
				message = message_to_html(reply_to.text,reply_to.entities)
				if test_note(bot,update, print_id, message):
					trigger_list = get_chat_config(chat_id,"Trigger_List")
					trigger_list[name]=message
					save_config_property(chat_id, "Trigger_List",trigger_list)
					bot_msg = TEXT[lang]["TRIGGER_ADD"]
				else:
					bot_msg = TEXT[lang]["NOTES_FAILED"]
			else:
				if len(args) >= 2:
					name = args[0]
					message=" ".join(update.message.text.split(" ")[2:])
					offset = len(update.message.text)-len(message)
					message = message_to_html(message,update.message.entities,offset)
					if test_note(bot,update, print_id, message):
						trigger_list = get_chat_config(chat_id,"Trigger_List")
						trigger_list[name]=message
						save_config_property(chat_id, "Trigger_List",trigger_list)
						bot_msg = TEXT[lang]["TRIGGER_ADD"]
					else:
						bot_msg = TEXT[lang]["NOTES_FAILED"]
				else:
					bot_msg = TEXT[lang]["TRIGGER_ADD_NOT_ARG"]
		elif not is_admin:
			 bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			 bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(user_id, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, False, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_delete_trigger(update: Update, context: CallbackContext):
	'''Command /delete_trigger message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
		if allow_command:
			if len(args) >= 1:
				trigger_list = get_chat_config(chat_id,"Trigger_List")
				for trigger in args:
					trigger_list.pop(trigger,"")
				save_config_property(chat_id, "Trigger_List",trigger_list)
				bot_msg = TEXT[lang]["TRIGGER_DELETE"]
			else:
				bot_msg = TEXT[lang]["TRIGGER_DELETE_NOT_ARG"]
		elif not is_admin:
			 bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			 bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(user_id, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_notes(update: Update, context: CallbackContext):
	'''Command /notes message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		lang = get_chat_config(chat_id, "Language")
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				public_list = get_public_list()
				reply_markup = InlineKeyboardMarkup(public_list)
				bot.send_message(chat_id, TEXT[lang]["PUBLIC_NOTES"],reply_markup=reply_markup)
				return
		trigger_list = get_chat_config(chat_id,"Trigger_List")
		trigger_string = "<b>Note List</b>\n\n"
		for key in trigger_list:
			trigger_string+="- <code>{}{}</code>\n".format(CONST["INIT_TRIGGER_CHAR"],key)
		bot_msg = trigger_string
		if chat_type == "private":
			bot.send_message(user_id, bot_msg, parse_mode=ParseMode.HTML)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_delete_question(update: Update, context: CallbackContext):
	'''Command /delete_trigger message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:                                                          allow_command = False
		if allow_command:
			if len(args) >= 1:
				question_list = get_chat_config(chat_id,"Question_List")
				for question in args:
					question_list.pop(question,"")
				save_config_property(chat_id, "Question_List",question_list)
				bot_msg = TEXT[lang]["QUESTION_DELETE"]
			else:
				bot_msg = TEXT[lang]["TRIGGER_DELETE_NOT_ARG"]
		elif not is_admin:
			 bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			 bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(user_id, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)
def cmd_questions(update: Update, context: CallbackContext):
	'''Command /add_trigger message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:                                                          allow_command = False
		if allow_command:
			question_list = get_chat_config(chat_id,"Question_List")
			question_string = "<b>Question List</b>\n\n"
			for key in question_list:
				question_string+="- {}\n".format(key)
			bot_msg = question_string
		elif not is_admin:
			 bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			 bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(user_id, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, True, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_add_question(update: Update, context: CallbackContext):
	'''Command /add_trigger message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		args = " ".join(args).split("|")
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
		if allow_command:
			if len(args) >=4:
				question = {}
				question["q"] = args[1]
				question["a"] = args[2]
				question["wrongs"] = []
				for wrong in args[3:]:
					question["wrongs"].append(wrong)
				questions = get_chat_config(chat_id, "Question_List")
				questions[args[0]] = question
				save_config_property(chat_id, "Question_List",questions)
				bot_msg = TEXT[lang]["QUESTION_ADD"]
			else:
				bot_msg = TEXT[lang]["QUESTION_ADD_NOT_ARG"]
		elif not is_admin:
			 bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			 bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(user_id, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, False, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_restrict_non_text(update: Update, context: CallbackContext):
	'''Command /restrict_non_text message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_id = chat_id
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		else:
			tlg_msg_to_selfdestruct(update.message)
		# Get actual chat configured language
		lang = get_chat_config(chat_id, "Language")
		# Check if the user is an Admin of the chat
		is_admin = tlg_user_is_admin(bot, user_id, chat_id)
		if is_admin is None:
			tlg_send_selfdestruct_msg(bot, print_id, TEXT[lang]["CAN_NOT_GET_ADMINS"], reply_to_message_id=update.message.message_id)
			return
		if not is_admin:
			tlg_send_selfdestruct_msg(bot, print_id, TEXT[lang]["CMD_NOT_ALLOW"], reply_to_message_id=update.message.message_id)
			return
		# Enable/Disable just text messages option
		if not get_chat_config(chat_id,"Restrict_Non_Text"):
			save_config_property(chat_id, "Restrict_Non_Text", True)
			tlg_send_selfdestruct_msg(bot, print_id, TEXT[lang]["RESTRICT_NON_TEXT_MSG_ENABLED"], reply_to_message_id=update.message.message_id)
		else:
			save_config_property(chat_id, "Restrict_Non_Text", False)
			tlg_send_selfdestruct_msg(bot, print_id, TEXT[lang]["RESTRICT_NON_TEXT_MSG_DISABLED"], reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def cmd_add_ignore(update: Update, context: CallbackContext):
	'''Command /add_ignore message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_id = chat_id
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
			
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
		if allow_command:
			if len(args) >= 1:
				ignore_list = get_chat_config(chat_id, "Ignore_List")
				try: # conversion of an incorrect string to integer can return ValueError
					user_id = int(args[0])
					# Ignore list limit enforcement
					if len(ignore_list) < CONST["IGNORE_LIST_MAX_ID"]:
						if user_id not in ignore_list:
							ignore_list.append(user_id)
							save_config_property(chat_id, "Ignore_List", ignore_list)
							bot_msg = TEXT[lang]["IGNORE_LIST_ADD_SUCCESS"]
						else:
							bot_msg = TEXT[lang]["IGNORE_LIST_ADD_DUPLICATED"]
					else:
						bot_msg = TEXT[lang]["IGNORE_LIST_ADD_LIMIT_EXCEEDED"]
				except ValueError:
					bot_msg = TEXT[lang]["IGNORE_LIST_ADD_INCORRECT_ID"]
			else:
				bot_msg = TEXT[lang]["IGNORE_LIST_ADD_NOT_ARG"]
		elif not is_admin:
			bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(print_id,bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, print_id, bot_msg, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def cmd_remove_ignore(update: Update, context: CallbackContext):
	'''Command /remove_ignore message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_id = chat_id
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
		if allow_command:
			if len(args) >= 1:
				ignore_list = get_chat_config(chat_id, "Ignore_List")
				try: # conversion of an incorrect string to integer can return ValueError
					user_id = int(args[0])
					try: # user_id can be absent in ignore_list
						index = ignore_list.index(user_id)
						del ignore_list[index]
						save_config_property(chat_id, "Ignore_List", ignore_list)
						bot_msg = TEXT[lang]["IGNORE_LIST_REMOVE_SUCCESS"]
					except ValueError:
						bot_msg = TEXT[lang]["IGNORE_LIST_REMOVE_NOT_IN_LIST"]
				except ValueError:
					bot_msg = TEXT[lang]["IGNORE_LIST_ADD_INCORRECT_ID"]
			else:
				bot_msg = TEXT[lang]["IGNORE_LIST_REMOVE_NOT_ARG"]
		elif not is_admin:
			bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(print_id,bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, print_id, bot_msg, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def cmd_ignore_list(update: Update, context: CallbackContext):
	'''Command /ignore_list message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_id = chat_id
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
		if allow_command:
			ignore_list = get_chat_config(chat_id, "Ignore_List")
			if not ignore_list:
				bot_msg = TEXT[lang]["IGNORE_LIST_EMPTY"]
			else:
				bot_msg = " ".join([str(x) for x in ignore_list])
		elif not is_admin:
			bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(print_id,bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, print_id, bot_msg, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def cmd_enable(update: Update, context: CallbackContext):
	'''Command /enable message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		enable = get_chat_config(chat_id, "Enabled")
		is_admin = tlg_user_is_admin(bot, user_id, chat_id)
		if is_admin:
			if enable:
				bot_msg = TEXT[lang]["ALREADY_ENABLE"]
			else:
				enable = True
				save_config_property(chat_id, "Enabled", enable)
				bot_msg = TEXT[lang]["ENABLE"]
		elif not is_admin:
			bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(user_id, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def cmd_disable(update: Update, context: CallbackContext):
	'''Command /disable message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		enable = get_chat_config(chat_id, "Enabled")
		is_admin = tlg_user_is_admin(bot, user_id, chat_id)
		if is_admin:
			if enable:
				enable = False
				save_config_property(chat_id, "Enabled", enable)
				bot_msg = TEXT[lang]["DISABLE"]
			else:
				bot_msg = TEXT[lang]["ALREADY_DISABLE"]
		elif not is_admin:
			bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(user_id, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def cmd_version(update: Update, context: CallbackContext):
	'''Command /version message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		chat_type = update.message.chat.type
		lang = get_chat_config(chat_id, "Language")
		bot_msg = TEXT[lang]["VERSION"].format(CONST["VERSION"])
		if chat_type == "private":
			bot.send_message(chat_id, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def cmd_about(update: Update, context: CallbackContext):
	'''Command /about handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		lang = get_chat_config(chat_id, "Language")
		bot_msg = TEXT[lang]["ABOUT_MSG"].format(CONST["REPOSITORY"],CONST["DEVELOPER"],CONST["ORG_DEVELOPER"],
			SECRETS["OWNER_NAME"])
		if update.message.chat.type == "private":
			bot.send_message(chat_id, bot_msg,parse_mode=ParseMode.HTML)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, reply_to_message_id=update.message.message_id)	
	except Exception as e:
		send_to_owner(bot,chat_id,e)


def cmd_captcha(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		captcha_level = get_chat_config(chat_id, "Captcha_Difficulty_Level")
		captcha_chars_mode = get_chat_config(chat_id, "Captcha_Chars_Mode")
		# Generate a pseudorandom captcha send it to telegram group and program message 
		# selfdestruct
		captcha = create_image_captcha(str(user_id), captcha_level, captcha_chars_mode)
		printts("[{}] Sending captcha message: {}...".format(chat_id, captcha["number"]))
		try:
			# Note: Img caption must be <= 1024 chars
			bot.send_photo(chat_id=chat_id, photo=open(captcha["image"],"rb"), timeout=20)
		except Exception as e:
			printts("[{}] {}".format(chat_id, str(e)))
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_protection(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		lang = get_chat_config(chat_id, "Language")
		print_id = chat_id
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		else:
			if tlg_user_is_admin(bot, user_id, chat_id):
				current = get_chat_config(chat_id,"Protected")
				if current:
					save_config_property(chat_id,"Protected",False)
					#link = revoke_group_link(bot,chat_id)
					bot_msg = TEXT[lang]["CHANNEL_PROTECTION_OFF"]
				else:
					save_config_property(chat_id,"Protected",True)
					bot_msg = TEXT[lang]["CHANNEL_PROTECTION_ON"]
				if chat_type == "private":
					bot.send_message(print_id, bot_msg,parse_mode=ParseMode.HTML)
				else:
					tlg_msg_to_selfdestruct(update.message)
					tlg_send_selfdestruct_msg(bot, print_id, bot_msg, reply_to_message_id=update.message.message_id)
			else:
				tlg_msg_to_selfdestruct(update.message)
				tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["CMD_NOT_ALLOW"],reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_trigger_delete_welcome(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_id = chat_id
		current = get_chat_config(chat_id,"Delete_Welcome")
		lang = get_chat_config(chat_id, "Language")
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
			if current:
				save_config_property(chat_id,"Delete_Welcome",False)
				bot_msg = TEXT[lang]["DELETE_WELCOME_OFF"]
			else:
				save_config_property(chat_id,"Delete_Welcome",True)
				bot_msg = TEXT[lang]["DELETE_WELCOME_ON"]
			bot.send_message(print_id, bot_msg,parse_mode=ParseMode.HTML)
		elif tlg_user_is_admin(bot, user_id, chat_id): 
			if current:
				save_config_property(chat_id,"Delete_Welcome",False)
				bot_msg = TEXT[lang]["DELETE_WELCOME_OFF"]
			else:
				save_config_property(chat_id,"Delete_Welcome",True)
				bot_msg = TEXT[lang]["DELETE_WELCOME_ON"]
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, print_id, bot_msg, reply_to_message_id=update.message.message_id)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["CMD_NOT_ALLOW"],reply_to_message_id=update.message.message_id)	
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_trigger_delete_notes(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_id = chat_id
		current = get_chat_config(chat_id,"Delete_Notes")
		lang = get_chat_config(chat_id, "Language")
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
			current = get_chat_config(chat_id,"Delete_Notes")
			if current:
				save_config_property(chat_id,"Delete_Notes",False)
				bot_msg = TEXT[lang]["DELETE_NOTES_OFF"]
			else:
				save_config_property(chat_id,"Delete_Notes",True)
				bot_msg = TEXT[lang]["DELETE_NOTES_ON"]
			bot.send_message(print_id, bot_msg,parse_mode=ParseMode.HTML)
		elif tlg_user_is_admin(bot, user_id, chat_id): 
			if current:
				save_config_property(chat_id,"Delete_Notes",False)
				bot_msg = TEXT[lang]["DELETE_NOTES_OFF"]
			else:
				save_config_property(chat_id,"Delete_Notes",True)
				bot_msg = TEXT[lang]["DELETE_NOTES_ON"]
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, print_id, bot_msg, reply_to_message_id=update.message.message_id)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["CMD_NOT_ALLOW"],reply_to_message_id=update.message.message_id)	
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_trigger_public_notes(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_id = chat_id
		lang = get_chat_config(chat_id, "Language")
		current = get_chat_config(chat_id,"Public_Notes")
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
			current = get_chat_config(chat_id,"Public_Notes")
			if current:
				save_config_property(chat_id,"Public_Notes",False)
				bot_msg = TEXT[lang]["PUBLIC_NOTES_OFF"]
			else:
				save_config_property(chat_id,"Public_Notes",True)
				bot_msg = TEXT[lang]["PUBLIC_NOTES_ON"]
			bot.send_message(print_id, bot_msg,parse_mode=ParseMode.HTML)
		elif tlg_user_is_admin(bot, user_id, chat_id): 
			if current:
				save_config_property(chat_id,"Public_Notes",False)
				bot_msg = TEXT[lang]["PUBLIC_NOTES_OFF"]
			else:
				save_config_property(chat_id,"Public_Notes",True)
				bot_msg = TEXT[lang]["PUBLIC_NOTES_ON"]
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, print_id, bot_msg, reply_to_message_id=update.message.message_id)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["CMD_NOT_ALLOW"],reply_to_message_id=update.message.message_id)	
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_trigger_bots(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_id = chat_id
		lang = get_chat_config(chat_id, "Language")
		current = get_chat_config(chat_id,"Allow_Bots")
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
			current = get_chat_config(chat_id,"Allow_Bots")
			if current:
				save_config_property(chat_id,"Allow_Bots",False)
				bot_msg = TEXT[lang]["ALLOW_BOTS_OFF"]
			else:
				save_config_property(chat_id,"Allow_Bots",True)
				bot_msg = TEXT[lang]["ALLOW_BOTS_ON"]
			bot.send_message(print_id, bot_msg,parse_mode=ParseMode.HTML)
		elif tlg_user_is_admin(bot, user_id, chat_id): 
			if current:
				save_config_property(chat_id,"Allow_Bots",False)
				bot_msg = TEXT[lang]["ALLOW_BOTS_OFF"]
			else:
				save_config_property(chat_id,"Allow_Bots",True)
				bot_msg = TEXT[lang]["ALLOW_BOTS_ON"]
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, print_id, bot_msg, reply_to_message_id=update.message.message_id)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["CMD_NOT_ALLOW"],reply_to_message_id=update.message.message_id)	
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_trigger_filters(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_id = chat_id
		lang = get_chat_config(chat_id, "Language")
		current = get_chat_config(chat_id,"Filters_Enabled")
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
			current = get_chat_config(chat_id,"Filters_Enabled")
			if current:
				save_config_property(chat_id,"Filters_Enabled",False)
				bot_msg = TEXT[lang]["ENABLE_FILTERS_OFF"]
			else:
				save_config_property(chat_id,"Filters_Enabled",True)
				bot_msg = TEXT[lang]["ENABLE_FILTERS_ON"]
			bot.send_message(print_id, bot_msg,parse_mode=ParseMode.HTML)
		elif tlg_user_is_admin(bot, user_id, chat_id): 
			if current:
				save_config_property(chat_id,"Filters_Enabled",False)
				bot_msg = TEXT[lang]["ENABLE_FILTERS_OFF"]
			else:
				save_config_property(chat_id,"Filters_Enabled",True)
				bot_msg = TEXT[lang]["ENABLE_FILTERS_ON"]
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, print_id, bot_msg, reply_to_message_id=update.message.message_id)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["CMD_NOT_ALLOW"],reply_to_message_id=update.message.message_id)	
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_add_filter(update: Update, context: CallbackContext):
	'''Command /add_filter message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		print_id = chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
		if allow_command:
			reply_to = getattr(update.message,"reply_to_message", None)
			if reply_to != None and len(args) >= 1:
				name = update.message.text[12:]
				message = message_to_html(reply_to.text,reply_to.entities)
				if test_note(bot,update, print_id, message):
					trigger_list = get_chat_config(chat_id,"Filter_List")
					trigger_list[name]=message
					save_config_property(chat_id, "Filter_List",trigger_list)
					bot_msg = TEXT[lang]["FILTER_ADD"]
				else:
					bot_msg = TEXT[lang]["FILTER_FAILED"]
			else:
				if len(args) >= 2:
					name = args[0]
					message=" ".join(update.message.text.split(" ")[2:])
					offset = len(update.message.text)-len(message)
					message = message_to_html(message,update.message.entities,offset)
					if test_note(bot,update, print_id, message):
						trigger_list = get_chat_config(chat_id,"Filter_List")
						trigger_list[name]=message
						save_config_property(chat_id, "Filter_List",trigger_list)
						bot_msg = TEXT[lang]["FILTER_ADD"]
					else:
						bot_msg = TEXT[lang]["FILTER_FAILED"]
				else:
					bot_msg = TEXT[lang]["FILTER_ADD_NOT_ARG"]
		elif not is_admin:
			 bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			 bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(user_id, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, False, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_delete_filter(update: Update, context: CallbackContext):
	'''Command /delete_trigger message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
		if allow_command:
			if len(args) >= 1:
				trigger_list = get_chat_config(chat_id,"Filter_List")
				for trigger in args:
					trigger_list.pop(trigger,"")
				save_config_property(chat_id, "Filter_List",trigger_list)
				bot_msg = TEXT[lang]["FILTER_DELETE"]
			else:
				bot_msg = TEXT[lang]["FILTER_DELETE_NOT_ARG"]
		elif not is_admin:
			 bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			 bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(user_id, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_copy_filter(update: Update, context: CallbackContext):
	'''Command /copy_filter message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		lang = get_chat_config(chat_id, "Language")
		allow_command = True
		if chat_type != "private":
			is_admin = tlg_user_is_admin(bot, user_id, chat_id)
			if not is_admin:
				allow_command = False
		if allow_command:
			if len(args) >= 2:
				filter_list = get_chat_config(chat_id,"Filter_List")
				note_list = get_chat_config(chat_id,"Trigger_List")

				if args[0] in note_list:
					filter_string = update.message.text.split(" ")[2]
					filter_list[filter_string]=note_list[args[0]]
					save_config_property(chat_id, "Filter_List", filter_list)
					bot_msg = TEXT[lang]["FILTER_CREATED"].format(filter_string,args[0])
				elif args[0] in filter_list:
					note_list=[args[1]]=filter_list[args[0]]
					save_config_property(chat_id, "Note_List", note_list)
					bot_msg = TEXT[lang]["NOTE_CREATED"].format(args[1],args[0])
				else:
					bot_msg = TEXT[lang]["COPY_FILTER_NOT_FOUND"]
			else:
				bot_msg = TEXT[lang]["FILTER_COPY_NOT_ARG"]
		elif not is_admin:
			 bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
		else:
			 bot_msg = TEXT[lang]["CAN_NOT_GET_ADMINS"]
		if chat_type == "private":
			bot.send_message(user_id, bot_msg)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_filters(update: Update, context: CallbackContext):
	'''Command /notes message handler'''
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		args = context.args
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		lang = get_chat_config(chat_id, "Language")
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		trigger_list = get_chat_config(chat_id,"Filter_List")
		trigger_string = "<b>Filter List</b>\n\n"
		for key in trigger_list:
			trigger_string+="- <code>{}</code>\n".format(key)
		bot_msg = trigger_string
		if chat_type == "private":
			bot.send_message(user_id, bot_msg, parse_mode=ParseMode.HTML)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_trigger_delete_info(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_id = chat_id
		lang = get_chat_config(chat_id, "Language")
		current = get_chat_config(chat_id,"Delete_Info")
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
			current = get_chat_config(chat_id,"Delete_Info")
			if current:
				save_config_property(chat_id,"Delete_Info",False)
				bot_msg = TEXT[lang]["DELETE_INFO_OFF"]
			else:
				save_config_property(chat_id,"Delete_Info",True)
				bot_msg = TEXT[lang]["DELETE_INFO_ON"]
			bot.send_message(print_id, bot_msg,parse_mode=ParseMode.HTML)
		elif tlg_user_is_admin(bot, user_id, chat_id): 
			if current:
				save_config_property(chat_id,"Delete_Info",False)
				bot_msg = TEXT[lang]["DELETE_INFO_OFF"]
			else:
				save_config_property(chat_id,"Delete_Info",True)
				bot_msg = TEXT[lang]["DELETE_INFO_ON"]
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, print_id, bot_msg, reply_to_message_id=update.message.message_id)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["CMD_NOT_ALLOW"],reply_to_message_id=update.message.message_id)	
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_info(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		msg =  update.message

		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_id = chat_id
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
		username = msg.from_user.username
		name = get_user_full_name(msg)
		lang = get_chat_config(chat_id, "Language")
		if chat_type == "private":
			bot_msg = TEXT[lang]["USER_INFO"].format(name,username,user_id,chat_id)
			bot.send_message(print_id, bot_msg,parse_mode=ParseMode.HTML)
		else:
			reply_to = getattr(update.message,"reply_to_message", None)
			reply_id = user_id
			if reply_to != None:
				reply_id = reply_to.from_user.id
				username = reply_to.from_user.username
				name = get_user_full_name(reply_to)
			bot_msg = TEXT[lang]["USER_INFO"].format(name,username,reply_id,chat_id)
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, bot_msg, reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_allow_group(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		msg =  update.message
		chat_id = update.message.chat_id
		lang = get_chat_config(chat_id, "Language")
		args = context.args
		if msg.chat.type == "private":
			if is_owner(msg.from_user.id):
				if len(args) >=1:
					group_str=""
					for group_id in args:
						save_config_property(int(group_id),"Allowed",True)
						group_str+="<code>{}</code>\n".format(group_id)

					bot_msg = TEXT[lang]["GROUP_ALLOWED"].format(group_str)
				else:
					bot_msg = TEXT[lang]["GROUP_ALLOW_NO_ARGS"]
			else:
				bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
			bot.send_message(chat_id, bot_msg,parse_mode=ParseMode.HTML)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["CMD_NOT_ALLOW"],reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_disallow_group(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		if delete_if_muted(bot,update):
			return
		msg =  update.message
		chat_id = update.message.chat_id
		lang = get_chat_config(chat_id, "Language")
		args = context.args
		if msg.chat.type == "private":
			if is_owner(msg.from_user.id):
				if len(args) >=1:
					group_str = ""
					for group_id in args:
						save_config_property(int(group_id),"Allowed",False)
						group_str+="<code>{}</code>\n".format(group_id)
					bot_msg = TEXT[lang]["GROUP_DISALLOWED"].format(group_str)
				else:
					bot_msg = TEXT[lang]["GROUP_DISALLOW_NO_ARGS"]
			else:
				bot_msg = TEXT[lang]["CMD_NOT_ALLOW"]
			bot.send_message(chat_id, bot_msg,parse_mode=ParseMode.HTML)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["CMD_NOT_ALLOW"],reply_to_message_id=update.message.message_id)
	except Exception as e:
		send_to_owner(bot,chat_id,e)

def cmd_mute(update: Update, context: CallbackContext):
	try:
		bot = context.bot
		args = context.args
		if delete_if_muted(bot,update):
			return
		chat_id = update.message.chat_id
		user_id = update.message.from_user.id
		chat_type = update.message.chat.type
		print_id = chat_id
		lang = get_chat_config(chat_id, "Language")
		if not tlg_user_is_admin(bot,user_id,chat_id):
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, chat_id, TEXT[lang]["CMD_NOT_ALLOW"],reply_to_message_id=update.message.message_id)
			return
		if chat_type == "private":
			connected = get_connected_group(bot,user_id)
			if connected < 0:
				chat_id = connected
			else:
				send_not_connected(bot,chat_id)
				return
		else:
			tlg_msg_to_selfdestruct(update.message)
		reply_to = getattr(update.message,"reply_to_message", None)
		bot_msg = TEXT[lang]["MUTE_ARGS_MISSING"]
		if reply_to != None:
			reply_id = reply_to.from_user.id
			args=[reply_id]
		if len(args) >=1:
			muted_list = get_chat_config(chat_id,"Muted_List")
			mute_time = get_chat_config(chat_id,"Mute_Time")
			for user_id in args:
				muted_list.append({"id": int(user_id),"time": time()+mute_time})
			save_config_property(chat_id,"Muted_List",muted_list)
			bot_msg = TEXT[lang]["MUTE_DONE"].format(math.floor(mute_time/60))
			

		if chat_type == "private":
			bot.send_message(print_id, bot_msg,parse_mode=ParseMode.HTML)
		else:
			tlg_msg_to_selfdestruct(update.message)
			tlg_send_selfdestruct_msg(bot, print_id, bot_msg, reply_to_message_id=update.message.message_id)	

	except Exception as e:
		send_to_owner(bot,chat_id,e)
####################################################################################################

### Main Loop Functions ###

def handle_remove_and_kicks(bot):
	'''Handle remove of sent messages and not verify new users ban'''
	while True:
		# Handle self-messages delete
		selfdestruct_messages(bot)
		# Check time for ban new users that has not completed the captcha
		check_time_to_kick_not_verify_users(bot)
		# Wait 10s (release CPU usage)
		sleep(10)


def selfdestruct_messages(bot):
	'''Handle remove messages sent by the Bot with the timed self-delete function'''
	global to_delete_in_time_messages_list
	# Check each Bot sent message
	i = 0
	while i < len(to_delete_in_time_messages_list):
		sent_msg = to_delete_in_time_messages_list[i]
		# If actual time is equal or more than the expected sent msg delete time
		if time() >= sent_msg["delete_time"]:
			printts("[{}] Scheduled deletion time for message: {}".format(
					sent_msg["Chat_id"], sent_msg["Msg_id"]))
			try:
				if bot.delete_message(sent_msg["Chat_id"], sent_msg["Msg_id"]):
					if sent_msg in to_delete_in_time_messages_list:
						to_delete_in_time_messages_list.remove(sent_msg)
			except Exception as e:
				printts("[{}] {}".format(sent_msg["Chat_id"], str(e)))
				# The bot has no privileges to delete messages
				if str(e) == "Message can't be deleted":
					lang = get_chat_config(sent_msg["Chat_id"], "Language")
					try:
						cant_del_msg = bot.send_message(sent_msg["Chat_id"],
								TEXT[lang]["CANT_DEL_MSG"], reply_to_message_id=sent_msg["Msg_id"])
						tlg_msg_to_selfdestruct(cant_del_msg)
					except Exception as ee:
						printts(str(e))
						printts(str(ee))
						pass
				if sent_msg in to_delete_in_time_messages_list:
					to_delete_in_time_messages_list.remove(sent_msg)
		i = i + 1


def check_time_to_kick_not_verify_users(bot):
	'''Check if the time for ban new users that has not completed the captcha has arrived'''
	global to_delete_join_messages_list
	global new_users_list
	i = 0
	while i < len(new_users_list):
		new_user = new_users_list[i]
		captcha_timeout = get_chat_config(new_user["chat_id"], "Captcha_Time")
		if new_user["kicked_ban"]:
			# Remove from new users list the remaining kicked users that have not solve the captcha 
			# in 1 hour (user ban just happen if a user try to join the group and fail to solve the 
			# captcha 5 times in the past hour)
			if time() >= (new_user["join_time"] + captcha_timeout*60) + 3600:
				# Remove user from new users list
				if new_user in new_users_list:
					new_users_list.remove(new_user)
		else:
			# If time for kick/ban has not arrived yet
			if time() < new_user["join_time"] + captcha_timeout*60:
				i = i + 1
				continue
			# The time has come for this user
			chat_id = new_user["chat_id"]
			lang = get_chat_config(chat_id, "Language")
			printts("[{}] Captcha reply timed out for user {}.".format(chat_id, new_user["user_name"]))
			# Check if this "user" has not join this chat more than 5 times (just kick)
			if new_user["join_retries"] < 5:
				printts("[{}] Captcha not solved, kicking {} ({})...".format(chat_id,
						new_user["user_name"], new_user["user_id"]))
				# Try to kick the user
				kick_result = tlg_kick_user(bot, new_user["chat_id"], new_user["user_id"])
				if kick_result == 1:
					# Kick success
					bot_msg = TEXT[lang]["NEW_USER_KICK"].format(new_user["user_name"])
					# Increase join retries
					new_user["join_retries"] = new_user["join_retries"] + 1
					printts("[{}] Increased join_retries to {}".format(chat_id,
							new_user["join_retries"]))
					# Set to auto-remove the kick message too, after a while
					tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)
				else:
					# Kick fail
					printts("[{}] Unable to kick".format(chat_id))
					if kick_result == -1:
						# The user is not in the chat
						bot_msg = TEXT[lang]['NEW_USER_KICK_NOT_IN_CHAT'].format(
								new_user["user_name"])
						# Set to auto-remove the kick message too, after a while
						tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)
					elif kick_result == -2:
						# Bot has no privileges to ban
						bot_msg = TEXT[lang]['NEW_USER_KICK_NOT_RIGHTS'].format(
								new_user["user_name"])
						# Send no rights for kick message without auto-remove
						try:
							bot.send_message(chat_id, bot_msg)
						except Exception as e:
							printts("[{}] {}".format(chat_id, str(e)))
					else:
						# For other reason, the Bot can't ban
						bot_msg = TEXT[lang]['BOT_CANT_KICK'].format(new_user["user_name"])
						# Set to auto-remove the kick message too, after a while
						tlg_send_selfdestruct_msg(bot, chat_id, bot_msg)
			# The user has join this chat 5 times and never succes to solve the captcha (ban)
			else:
				printts("[{}] Captcha not solved, banning {} ({})...".format(chat_id,
						new_user["user_name"], new_user["user_id"]))
				# Try to ban the user and notify Admins
				ban_result = tlg_ban_user(bot, chat_id, new_user["user_id"])
				# Remove user from new users list
				if new_user in new_users_list:
					new_users_list.remove(new_user)
				if ban_result == 1:
					# Ban success
					bot_msg = TEXT[lang]["NEW_USER_BAN"].format(new_user["user_name"])
				else:
					# Ban fail
					if ban_result == -1:
						# The user is not in the chat
						bot_msg = TEXT[lang]['NEW_USER_BAN_NOT_IN_CHAT'].format(
								new_user["user_name"])
					elif ban_result == -2:
						# Bot has no privileges to ban
						bot_msg = TEXT[lang]['NEW_USER_BAN_NOT_RIGHTS'].format(
								new_user["user_name"])
					else:
						# For other reason, the Bot can't ban
						bot_msg = TEXT[lang]['BOT_CANT_BAN'].format(new_user["user_name"])
				# Send ban notify message
				printts("[{}] {}".format(chat_id, bot_msg))
				try:
					bot.send_message(chat_id, bot_msg)
				except Exception as e:
					printts("[{}] {}".format(chat_id, str(e)))
			# Update user info (join_retries & kick_ban)
			new_user["kicked_ban"] = True
			if new_user in new_users_list:
				pos = new_users_list.index(new_user)
				new_users_list[pos] = new_user
			# Remove join messages
			printts("[{}] Removing messages from user {}...".format(chat_id, new_user["user_name"]))
			j = 0
			while j < len(to_delete_join_messages_list):
				msg = to_delete_join_messages_list[j]
				if msg["user_id"] == new_user["user_id"]:
					if msg["chat_id"] == new_user["chat_id"]:
						# Uncomment next line to remove "user join" message too
						#tlg_delete_msg(bot, msg["chat_id"], msg["msg_id_join0"].message_id)
						tlg_delete_msg(bot, msg["chat_id"], msg["msg_id_join1"])
						tlg_delete_msg(bot, msg["chat_id"], msg["msg_id_join2"])
						tlg_msg_to_selfdestruct(msg["msg_id_join0"])
						if msg in to_delete_join_messages_list:
							to_delete_join_messages_list.remove(msg)
						break
				j = j + 1
			printts("[{}] Kick/Ban process complete".format(chat_id))
			printts(" ")
		i = i + 1

####################################################################################################

### Main Function ###

def main():
	'''Main Function'''
	# Check if Bot Token has been set or has default value
	if SECRETS["TOKEN"] == "XXXXXXXXX:XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX":
		printts("Error: Bot Token has not been set.")
		printts("Please copy 'secrets.example.py' to 'secrets.py' and add your Bot Token to 'secrets.py'.")
		printts("Exit.\n")
		exit(0)
	printts("Bot started.")
	# Initialize resources by populating files list and configs with chats found files
	initialize_resources()
	printts("Resources initialized.")
	# Set messages to be sent silently by default
	msgs_defaults = Defaults(disable_notification=True)
	# Create an event handler (updater) for a Bot with the given Token and get the dispatcher
	updater = Updater(SECRETS["TOKEN"], use_context=True, defaults=msgs_defaults)
	dp = updater.dispatcher
	# Set to dispatcher all expected commands messages handler
	dp.add_handler(CommandHandler("start", cmd_start))
	dp.add_handler(CommandHandler("info", cmd_info))

	dp.add_handler(CommandHandler("commands", cmd_commands))
	dp.add_handler(CommandHandler("time", cmd_time, pass_args=True))
	dp.add_handler(CommandHandler("difficulty", cmd_difficulty, pass_args=True))
	dp.add_handler(CommandHandler("captcha_mode", cmd_captcha_mode, pass_args=True))
	dp.add_handler(CommandHandler("set_welcome_msg", cmd_set_welcome_message, pass_args=True))
	dp.add_handler(CommandHandler("welcome_msg", cmd_welcome_message))
	dp.add_handler(CommandHandler("protection", cmd_protection))
	dp.add_handler(CommandHandler("connect", cmd_connect, pass_args=True))
	dp.add_handler(CommandHandler("disconnect", cmd_disconnect))

	#troll commands -> kick ;)
	dp.add_handler(CommandHandler("kill_yourself",cmd_kick))

	dp.add_handler(CommandHandler("trigger_delete_welcome", cmd_trigger_delete_welcome))
	dp.add_handler(CommandHandler("trigger_bots", cmd_trigger_bots))
	dp.add_handler(CommandHandler("trigger_delete_notes", cmd_trigger_delete_notes))
	dp.add_handler(CommandHandler("trigger_public_notes",cmd_trigger_public_notes))
	dp.add_handler(CommandHandler("trigger_delete_info",cmd_trigger_delete_info))


	dp.add_handler(CommandHandler("trigger_filters",cmd_trigger_filters))
	dp.add_handler(CommandHandler("add_filter", cmd_add_filter,pass_args=True))
	dp.add_handler(CommandHandler("delete_filter",cmd_delete_filter,pass_args=True))
	dp.add_handler(CommandHandler("filters",cmd_filters,pass_args=True))
	dp.add_handler(CommandHandler("copy_filter",cmd_copy_filter,pass_args=True))

	dp.add_handler(CommandHandler("allow_group", cmd_allow_group,pass_args=True))
	dp.add_handler(CommandHandler("disallow_group", cmd_disallow_group,pass_args=True))

	dp.add_handler(CommandHandler("add_note", cmd_add_trigger,pass_args=True))
	dp.add_handler(CommandHandler("delete_note",cmd_delete_trigger,pass_args=True))
	dp.add_handler(CommandHandler("notes",cmd_notes,pass_args=True))

	dp.add_handler(CommandHandler("add_question", cmd_add_question, pass_args=True))
	dp.add_handler(CommandHandler("delete_question", cmd_delete_question, pass_args=True))
	dp.add_handler(CommandHandler("questions", cmd_questions, pass_args=True))

	dp.add_handler(CommandHandler("mute", cmd_mute, pass_args=True))

	dp.add_handler(CommandHandler("restrict_non_text", cmd_restrict_non_text, pass_args=True))
	dp.add_handler(CommandHandler("add_ignore", cmd_add_ignore, pass_args=True))
	dp.add_handler(CommandHandler("remove_ignore", cmd_remove_ignore, pass_args=True))
	dp.add_handler(CommandHandler("ignore_list", cmd_ignore_list))
	dp.add_handler(CommandHandler("enable", cmd_enable))
	dp.add_handler(CommandHandler("disable", cmd_disable))
	dp.add_handler(CommandHandler("version", cmd_version))
	dp.add_handler(CommandHandler("about", cmd_about))
	#dp.add_handler(CommandHandler("captcha", cmd_captcha)) # Just for debug
	# Set to dispatcher a not-command text messages handler
	dp.add_handler(MessageHandler(Filters.text, msg_nocmd))
	# Set to dispatcher not text messages handler
	dp.add_handler(MessageHandler(Filters.photo | Filters.audio | Filters.voice |
			Filters.video | Filters.sticker | Filters.document | Filters.location |
			Filters.contact, msg_notext))

	# Set to dispatcher a new member join the group and member left the group events handlers
	dp.add_handler(MessageHandler(Filters.status_update.new_chat_members, msg_new_user))

	#dp.add_handler(MessageHandler(Filters.status_update, handle_service_message))

	# Set to dispatcher request new captcha button callback handler
	dp.add_handler(CallbackQueryHandler(button_request_captcha))
	# Launch the Bot ignoring pending messages (clean=True) and get all updates (cllowed_uptades=[])
	updater.start_polling(clean=True, allowed_updates=[])
	printts("Bot setup completed. Bot is now running.")
	# Handle remove of sent messages and not verify new users ban (main loop)
	handle_remove_and_kicks(updater.bot)


if __name__ == "__main__":
	main()

### End Of Code ###
