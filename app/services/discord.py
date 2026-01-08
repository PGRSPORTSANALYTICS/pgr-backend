from typing import Optional
import httpx

class DiscordService:
    def __init__(self, bot_token: Optional[str] = None, guild_id: Optional[str] = None):
        self.bot_token = bot_token
        self.guild_id = guild_id
        self.base_url = "https://discord.com/api/v10"
    
    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.guild_id)
    
    async def assign_role(self, discord_user_id: str, role_id: str) -> bool:
        if not self.is_configured:
            raise ValueError("Discord bot not configured. Set DISCORD_BOT_TOKEN and DISCORD_GUILD_ID.")
        
        url = f"{self.base_url}/guilds/{self.guild_id}/members/{discord_user_id}/roles/{role_id}"
        headers = {
            "Authorization": f"Bot {self.bot_token}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.put(url, headers=headers)
            return response.status_code == 204
    
    async def remove_role(self, discord_user_id: str, role_id: str) -> bool:
        if not self.is_configured:
            raise ValueError("Discord bot not configured. Set DISCORD_BOT_TOKEN and DISCORD_GUILD_ID.")
        
        url = f"{self.base_url}/guilds/{self.guild_id}/members/{discord_user_id}/roles/{role_id}"
        headers = {
            "Authorization": f"Bot {self.bot_token}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.delete(url, headers=headers)
            return response.status_code == 204
    
    async def get_member(self, discord_user_id: str) -> Optional[dict]:
        if not self.is_configured:
            raise ValueError("Discord bot not configured. Set DISCORD_BOT_TOKEN and DISCORD_GUILD_ID.")
        
        url = f"{self.base_url}/guilds/{self.guild_id}/members/{discord_user_id}"
        headers = {
            "Authorization": f"Bot {self.bot_token}"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return response.json()
            return None

discord_service = DiscordService()
